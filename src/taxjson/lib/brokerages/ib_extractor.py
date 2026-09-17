import csv
import re
import sys
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timedelta


# IB currency → exchange-suffix map. Previously duplicated inline at three
# sites; an unmapped currency (EUR/JPY/CHF/...) silently produced a junk
# suffix like SYM.EUR that no downstream ticker map recognizes, fragmenting
# the position. Centralized + a one-time warning so a held exotic currency is
# surfaced instead of corrupting silently.
_IB_CURRENCY_EXT = {'CAD': 'TO', 'USD': 'US', 'AUD': 'AX', 'GBP': 'L'}
_IB_WARNED_CURRENCIES: set = set()


def _ib_currency_ext(currency: str) -> str:
    ext = _IB_CURRENCY_EXT.get(currency)
    if ext is None:
        if currency not in _IB_WARNED_CURRENCIES:
            _IB_WARNED_CURRENCIES.add(currency)
            print(
                f"warning: IB currency {currency!r} has no exchange-suffix "
                f"mapping; using '.{currency}', which downstream ticker maps "
                f"won't recognize (position may fragment). Add it to "
                f"_IB_CURRENCY_EXT in ib_extractor.py.",
                file=sys.stderr,
            )
        return currency
    return ext


# Trades take their exchange suffix from the trade currency; DIVIDEND/TAX rows
# take theirs from the security's ISIN country. Those disagree for a dual-listed
# name held on a non-domicile exchange — a Canadian-domiciled B2Gold held on the
# NYSE (BTG.US) has its dividend stamped BTG.TO (ISIN 'CA'); an Irish-domiciled
# Seagate held as STX.US gets STX.L (ISIN 'IE') — orphaning the income onto a
# phantom symbol with no shares. Neither currency nor ISIN is reliable on its
# own (B2Gold's TSX listing BTO.TO pays some dividends in USD, so currency would
# wrongly say .US there). The dividend's own ticker (BTO vs BTG) already names
# the listing, so bind each income row to the suffix of the position actually
# held for that ticker in this statement.
_POSITION_ACTIONS = frozenset({'BUYSELL', 'TRANSFER'})
_INCOME_ACTIONS = frozenset({'DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'})
_KNOWN_EXT_RE = re.compile(r'^(.*)\.(TO|US|AX|L)$')

# A Corporate Actions row settling the fractional share a split/merger
# ratio leaves over for cash: negative Quantity (the fraction removed),
# the cash in Proceeds. Matched on the phrase alone, wherever it sits
# after the leading `TICKER(ISIN)` token — IB's exact wording for this
# row is unverified against a real statement (KNOWN_ISSUES.md).
_IB_CIL_RE = re.compile(
    r'^\s*([A-Z0-9\s\.]+?)\s*\([^)]*\).*?\bcash\s+in\s+lieu\b', re.IGNORECASE)


def _ib_refine_split_ratio(info: Dict[str, Any]) -> None:
    """Snap an emitted SPLIT to the broker's own leg quantities once both
    legs are known. IB books fractional results to 4 dp, so the text
    ratio leaves ±1e-4 phantom dust; and a whole-share floor with cash
    in lieu (10 sh 1-for-3 → 3 sh + 0.3333 CIL) diverges from the text
    ratio by the settled fraction, so that fraction counts as part of
    the new leg: old × ratio − fraction then lands on whole shares
    exactly. Bounded to near the text ratio — a larger divergence means
    the legs describe something else; keep the text ratio."""
    if not (info['new'] and info['old']):
        return
    derived = (info['new'] + info['cil']) / info['old']
    if abs(derived / info['text_ratio'] - 1.0) < 0.05:
        info['tx']['quantity'] = derived


def _split_known_ext(symbol: str):
    """(root, ext) if `symbol` ends in a known exchange suffix, else (symbol,
    None). Options (OCC) and futures (F:/CASH) have no such suffix → skipped."""
    m = _KNOWN_EXT_RE.match(symbol or '')
    return (m.group(1), m.group(2)) if m else (symbol or '', None)


def _reattribute_income_to_holdings(transactions: List[Dict[str, Any]],
                                    extra_held=None) -> None:
    """Rewrite each DIVIDEND/TAX suffix to match the position held for the same
    ticker in this file, correcting ISIN-vs-listing mismatches. Leaves the
    ISIN-derived suffix untouched when the account holds no position for that
    ticker, or holds it under more than one suffix (ambiguous — don't guess).

    `extra_held` seeds the holdings map with full symbols from the statement's
    Open Positions section. That closes the two gaps of trade-only inference:
    (a) a buy-and-hold name generates dividends in every LATER statement year
    with no trade rows, so trade-derived holdings were empty and the ISIN
    fallback (STX.L, BTG.TO) came back every hold-year; (b) an interlisted
    same-ticker name (ENB-class) whose OTHER listing is still held makes the
    root ambiguous, correctly suppressing the rewrite instead of misbinding a
    .TO dividend onto the .US listing traded in this file."""
    held: Dict[str, set] = {}
    for full in (extra_held or ()):
        root, ext = _split_known_ext(full)
        if ext:
            held.setdefault(root, set()).add(ext)
    for t in transactions:
        if t.get('action') in _POSITION_ACTIONS:
            root, ext = _split_known_ext(t.get('symbol'))
            if ext:
                held.setdefault(root, set()).add(ext)
    if not held:
        return
    for t in transactions:
        # ROC ADJUSTs come out of the same ISIN-suffixed Dividends section
        # as income rows, so they need the same held-listing rebind — an
        # ADJUST on a phantom listing (STX.L) would reduce nothing.
        is_roc_adjust = (t.get('action') == 'ADJUST'
                         and t.get('type') == 'roc')
        if t.get('action') not in _INCOME_ACTIONS and not is_roc_adjust:
            continue
        root, ext = _split_known_ext(t.get('symbol'))
        if ext is None:
            continue
        suffixes = held.get(root)
        if suffixes and len(suffixes) == 1:
            want = next(iter(suffixes))
            if want != ext:
                t['symbol'] = f"{root}.{want}"


def get_ib_settlement(date_str: str, asset_cat: str,
                      currency: str = 'USD') -> str:
    """
    Logic from ib_trades.pl:
    - Default T+1
    - Stocks/Warrants before the T+1 cutover are T+2. The cutover is
      MARKET-specific, keyed by trade currency: US 2024-05-28; Canada
      2024-05-27 (a TSX trading day — the US was closed for Memorial Day).
    - Skips weekends (exchange holidays are NOT modeled — documented
      limitation)
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str

    cutover = ('2024-05-27' if (currency or '').upper() == 'CAD'
               else '2024-05-28')
    days_to_add = 1
    if asset_cat in ('Stocks', 'Warrants') and date_str < cutover:
        days_to_add = 2
        
    added = 0
    curr = dt
    while added < days_to_add:
        curr += timedelta(days=1)
        if curr.weekday() < 5: # Monday-Friday
            added += 1
            
    return curr.strftime("%Y-%m-%d")

from taxjson.lib.brokerages.base import BaseBrokerage, _parse_div_qty_rate, encode_occ_strike, is_roc_description
from taxjson.lib.corp_actions import ib_tender_root

# `Commission Adjustments` description: `Refund (KWEB, -200 2025-02-07)`
# — the ticker sits first inside the parenthetical.
_IB_COMM_ADJ_TICKER_RE = re.compile(r'\(\s*([A-Z0-9][A-Z0-9 .\-]*?)\s*,')


class IbBrokerage(BaseBrokerage):
    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        transactions = []
        # Corporate Actions rows that aren't SPLIT or Spinoff (e.g.
        # Mergers/Acquisitions, name changes) aren't auto-translated to
        # transactions — the row formats are too varied to handle safely
        # without per-event judgement. Track them per ticker so we can
        # surface a warning at end of parse, prompting the user to add a
        # manual TRANSFER entry if the event affects taxable basis.
        unhandled_ca_tickers: Dict[str, int] = {}

        # Fee rows that lack both a Date column and a "for Mmm YYYY" hint
        # in the description. Collected and reported as one summary line
        # at the end of parse_file so the user sees a single audible
        # signal instead of one warning per skipped fee.
        skipped_dateless_fees: List[float] = []

        # (symbol, date, ratio) of SPLIT rows already emitted from the
        # Corporate Actions section — a reverse split's paired negative+
        # positive legs must yield ONE SPLIT row. Each entry keeps the
        # emitted transaction plus the legs' share quantities so the
        # ratio can be refined to the broker's own rounding once both
        # legs have been seen (IB books fractional results to 4 dp:
        # 5,000 shares 1-for-3 becomes 1666.6667, not 5000/3 — using
        # the text ratio leaves ±1e-4 phantom dust in the pool).
        emitted_splits: Dict[tuple, Dict[str, Any]] = {}
        # Cash-in-lieu fractions (per symbol) seen BEFORE their split's
        # legs in the file — folded into the split's ratio when it is
        # emitted (see _ib_refine_split_ratio).
        pending_cil: Dict[str, float] = {}

        # Full symbols from the Open Positions section, used to seed the
        # income-reattribution holdings map (covers buy-and-hold years with
        # no trade rows, and disambiguates interlisted same-ticker names).
        open_position_syms: set = set()

        # Year-end dividend accruals. IB accrues a declared dividend
        # ('Po' code) and reverses the accrual ('Re') once the cash
        # actually posts into the Dividends section. We do NOT treat an
        # accrual as income — it's an estimate (often a stale per-share
        # rate, and denominated in the account base currency rather than
        # the dividend's native currency), and booking it would also risk
        # double-counting once the real Dividends row lands in a later
        # statement. Instead we net Po/Re per dividend and, at end of
        # parse, warn about any accrual that's still open and has no
        # matching posted Dividends row in this file — that's income IB
        # has declared but not yet booked, so the user should re-download
        # a more recent statement before filing.
        accrual_net: Dict[tuple, float] = {}
        accrual_meta: Dict[tuple, Dict[str, str]] = {}
        # (symbol, pay_date) -> per-share Gross Rate from the accruals section.
        # Payment-in-Lieu rows carry no rate in their own description, but the
        # accrual for the same dividend does — used to back-fill PIL qty/price.
        accrual_rate: Dict[tuple, float] = {}
        # (ticker, date) of every posted Dividends row in this file, used
        # to suppress an accrual warning when the cash dividend is already
        # present. The Dividends row's Date is the dividend's pay date.
        posted_dividend_keys = set()
        # Statement period end (ISO). An accrual whose pay date is after
        # the period end is just a normal pending dividend, not a missed
        # one — only accruals payable *within* the period are surprising.
        statement_period_end = ''

        # Explicit currency conversions (Trades / Forex rows, e.g.
        # USD.CAD). Counted, never translated: no downstream consumer
        # models a cash conversion as a disposition (`taxjson fx-cash`
        # reconstructs FX cash gains from security cash flows only —
        # KNOWN_ISSUES "IB Trades/Forex conversions are not modeled").
        # Emitting them as a BUYSELL of a phantom `USD`/`CASH.USD`
        # asset would corrupt the position book, so they get one
        # explicit note instead of vanishing into the asset filter.
        forex_rows = 0

        # `Transaction Fees` rows (UK Stamp Tax and the like): a
        # per-trade levy IB books OUTSIDE the trade's Comm/Fee column.
        # A standalone FEE row never folds into ACB (core.py skips FEE
        # before the pool walk), so each one is folded into the
        # same-day BUYSELL on its symbol once the whole file is read;
        # only an unmatched levy becomes a symbol-bound FEE row.
        pending_txn_fees: List[Dict[str, Any]] = []

        # Tender / voluntary-offer journals (Corporate Actions rows
        # matched by corp_actions.ib_tender_root), keyed by the
        # suffixed ROOT symbol. IB parks tendered shares on a `.TEN`
        # placeholder line with zero-proceeds legs and journals them
        # back (or settles them) on allocation. Zero-proceeds legs are
        # recognized non-events (the position never economically
        # changed); a leg carrying cash is a DISPOSITION and is booked
        # as a sale — never dropped into the unhandled tally. Per root:
        # rows seen, shares still parked on the `.TEN` line, cash
        # dispositions booked, dates — for the end-of-parse notes.
        tender_legs: Dict[str, Dict[str, Any]] = {}

        # Row-level accounting (base.py zero-drop policy): every Data
        # row is either consumed (emitted, or read into parser state)
        # or counted under a skip label, so `taxjson-brokerage --lint`
        # can reconcile rows_seen == consumed + skipped. Labels with the
        # KNOWN_NONEVENT_PREFIX are rows the parser recognizes as
        # non-events (subtotals, metadata sections) and reports in the
        # calmer of the two summary notes.
        self._rows_seen = 0
        _NE = self.KNOWN_NONEVENT_PREFIX

        # utf-8-sig handles a BOM gracefully — IB's web export occasionally
        # ships one and plain utf-8 reads it as a data byte, breaking the
        # very first section-tag match.
        with open(path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header_maps = {} # section -> header_map
            # Whether this file's Trades section carries Order-level
            # rows (DataDiscriminator): once seen, execution-level
            # 'Trade' rows are duplicates and are skipped.
            _seen_order_level = False

            for row in reader:
                if not row:
                    continue

                section = row[0]
                type_ = row[1] if len(row) > 1 else ''

                if type_ == 'Header':
                    header_maps[section] = {col: i for i, col in enumerate(row)}
                    continue

                if type_ != 'Data':
                    continue

                self._rows_seen += 1

                header_map = header_maps.get(section)
                if not header_map:
                    self.count_skip(f"section {section}: Data row with "
                                    f"no Header row")
                    continue

                if section == 'Trades' or section == 'Options Expirations':
                    asset_cat = row[header_map.get('Asset Category', 0)] if section == 'Trades' else 'Equity and Index Options'

                    # Roll-up rows carry the asset category of what
                    # they sum (`Total,Forex,...`), so they must be
                    # recognized BEFORE the per-category dispatch or a
                    # Forex subtotal counts as one more conversion.
                    _pre_disc = ((row[header_map['DataDiscriminator']]
                                  or '').strip()
                                 if 'DataDiscriminator' in header_map
                                 and header_map['DataDiscriminator'] < len(row)
                                 else '')
                    if _pre_disc in ('SubTotal', 'Total'):
                        self.count_skip(f"{_NE}Trades roll-up row "
                                        f"({_pre_disc})")
                        continue

                    if asset_cat not in ('Stocks', 'Equity and Index Options', 'Futures', 'Options On Futures', 'Warrants'):
                        if asset_cat == 'Forex':
                            forex_rows += 1
                            self.count_skip(
                                f"{_NE}Trades/Forex (currency conversion, "
                                f"not modeled — KNOWN_ISSUES)")
                        elif asset_cat in ('Total', '') or 'Total' in asset_cat:
                            self.count_skip(f"{_NE}Trades subtotal row")
                        else:
                            # Bonds, CFDs, ... — a real asset class the
                            # parser has no branch for: loud bucket.
                            self.count_skip(f"Trades/{asset_cat}")
                        continue

                    # Flex queries configured with several detail levels
                    # list each fill more than once (Order + Trade +
                    # ClosedLot rows for the SAME execution); without
                    # this filter each level emitted a BUYSELL and
                    # split-fill disambiguation then PROTECTED the
                    # duplicate from dedup — every trade double-counted.
                    # Accept exactly one level, preferring 'Order' when
                    # both appear; roll-up rows are skipped silently
                    # (they are structure, not data).
                    if 'DataDiscriminator' in header_map:
                        _disc = (row[header_map['DataDiscriminator']]
                                 or '').strip()
                        if _disc in ('ClosedLot', 'SubTotal', 'Total'):
                            self.count_skip(f"{_NE}Trades roll-up row "
                                            f"({_disc})")
                            continue
                        if _disc == 'Trade' and _seen_order_level:
                            self.count_skip(f"{_NE}Trades execution-level "
                                            f"duplicate of an Order row")
                            continue
                        if _disc == 'Order':
                            _seen_order_level = True
                        elif _disc and _disc not in ('Order', 'Trade'):
                            self.count_skip(f"Trades DataDiscriminator "
                                            f"{_disc}")
                            continue
                    
                    # Wrap header-derived AND numeric cell reads so a
                    # malformed row skips with a warning instead of
                    # aborting the whole parse. The header-derived
                    # reads previously fell back to row[0] (= the
                    # literal section name like 'Trades') via
                    # `.get(key, 0)` on missing columns, silently
                    # emitting a junk 'Trades.US' transaction; using
                    # strict `header_map[key]` lookups raises KeyError
                    # which the except now catches and skips loudly.
                    try:
                        symbol = row[header_map['Symbol']]
                        # Preserve the raw symbol string as `description`
                        # so taxjson-brokerage's security-overrides can
                        # rewrite mislabeled IB tickers (every other
                        # broker emits a `description` field; IB Trades
                        # was the lone gap — without it the override
                        # silently no-op'd on IB rows). For options the
                        # raw symbol is the verbose `BCE 16JAN26 100 P`
                        # form, which is exactly what the user keys on
                        # in `ticker_extraction_overrides.txt`.
                        description = symbol
                        currency = row[header_map['Currency']]
                        date_time_str = row[header_map['Date/Time']]
                        parts = date_time_str.replace(',', '').split()
                        date = parts[0] if parts else ''
                        time = parts[1] if len(parts) > 1 else '09:30:00'
                        date_settle = get_ib_settlement(date, asset_cat, currency)
                        qty = float(row[header_map['Quantity']].replace(',', ''))
                        price = float(row[header_map.get('T. Price', 0)].replace(',', '')) if 'T. Price' in header_map else 0.0
                        proceeds_key = 'Proceeds' if 'Proceeds' in header_map else 'Notional Value'
                        gross_proceeds = abs(float(row[header_map.get(proceeds_key, 0)].replace(',', ''))) if proceeds_key in header_map else 0.0
                        comm_fee = abs(float(row[header_map.get('Comm/Fee', 0)].replace(',', ''))) if 'Comm/Fee' in header_map else 0.0
                    except (ValueError, IndexError, KeyError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue

                    # Cancelled trades: IB books a reversal row coded
                    # `Ca` that negates the original's qty/proceeds and
                    # REFUNDS its commission. abs() on the commission
                    # turned the refund into a second charge, so a
                    # cancelled (never-executed) trade left a phantom
                    # round trip losing 2x commission — and two phantom
                    # rows feeding the superficial-loss windows. With
                    # the commission sign preserved the pair nets to
                    # zero; the rows still cancel out of the pool walk.
                    _pre_code = (row[header_map.get('Code', 0)]
                                 if 'Code' in header_map else '')
                    if 'Ca' in re.split(r'[;,\s]+', _pre_code or ''):
                        _raw_comm = (row[header_map['Comm/Fee']]
                                     .replace(',', '')
                                     if 'Comm/Fee' in header_map else '0')
                        try:
                            _signed_comm = float(_raw_comm or 0)
                        except ValueError:
                            _signed_comm = 0.0
                        # IB reports charges negative; a refund is
                        # positive. Recompute net with the true sign.
                        comm_fee = -_signed_comm

                    net_amount = (gross_proceeds + comm_fee) if qty > 0 else (gross_proceeds - comm_fee)
                    action = 'BUYSELL'
                    # IB packs multiple per-trade codes into one cell
                    # (separators are `;`, `,`, or whitespace) — e.g.
                    # `O;P`, `A`, `C;Ep`. Split into tokens and match `A`
                    # exactly so codes that merely *contain* the letter
                    # A (e.g. `Au`, `AEx`, `ADR`) don't get reclassified
                    # as an option assignment. The zero-price guard
                    # already limits damage, but the token match is the
                    # principled check.
                    code = row[header_map.get('Code', 0)] if 'Code' in header_map else ''
                    code_tokens = re.split(r'[;,\s]+', code)
                    # `Ex` (exercise) is the holder-side twin of `A`
                    # (assignment): IB closes the option leg at T. Price
                    # 0 with `C;Ex` and books the stock leg (`Ex;O` for
                    # a call, `C;Ex` for a put) at the strike. Booked as
                    # a plain BUYSELL, the option's premium was realized
                    # as a 100% loss on the option instead of rolling
                    # into the stock leg's cost (call) / proceeds (put)
                    # the way an assignment's does. The zero-price
                    # guard keeps the stock leg (price = strike) a
                    # BUYSELL, which is the leg that pops the premium.
                    if (('A' in code_tokens or 'Ex' in code_tokens)
                            and abs(price) < 1e-5):
                        action = 'ASSIGN'
                    
                    if asset_cat in ('Equity and Index Options', 'Options On Futures'):
                        # Format 1: Equity Options (Standard) or Futures Options (e.g. "XSP 16JAN26 68.5 P")
                        opt_match = re.search(r'^(.+?)\s+(\d{2})([A-Z]{3})(\d{2})\s+([\d\.]+)\s+([PC])$', symbol)
                        
                        # Format 3: Futures Options Monthly (e.g. "CL JAN26 52 P")
                        opt_match_monthly = re.search(r'^(.+?)\s+([A-Z]{3})(\d{2})\s+([\d\.]+)\s+([PC])$', symbol) if not opt_match else None
                        
                        # Format 4: Legacy IB format (e.g. "SPX 20241220 P 4000")
                        opt_match_legacy = re.search(r'^(.+?)\s+(\d{8})\s+([PC])\s+([\d\.]+)', symbol) if not (opt_match or opt_match_monthly) else None
                        
                        mon_map = {
                            'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                            'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
                        }

                        if opt_match:
                            base, day, mon, yr, strike, right = opt_match.groups()
                            month = mon_map.get(mon.upper())
                            if month:
                                base = base.replace(' ', '.')
                                symbol = f"{base}{yr}{month}{day}{right}{encode_occ_strike(strike)}"
                        elif opt_match_monthly:
                            base, mon, yr, strike, right = opt_match_monthly.groups()
                            month = mon_map.get(mon.upper())
                            if month:
                                base = base.replace(' ', '.')
                                # Use "20" as a placeholder day for monthly options
                                symbol = f"{base}{yr}{month}20{right}{encode_occ_strike(strike)}"
                        elif opt_match_legacy:
                            base, exp, right, strike = opt_match_legacy.groups()
                            base = base.replace(' ', '.')
                            symbol = f"{base}{exp[2:]}{right}{encode_occ_strike(strike)}"
                            
                        # Strip all spaces as a fallback / cleanup
                        symbol = symbol.replace(' ', '')
                        
                        if asset_cat == 'Options On Futures':
                            symbol = f"F:{symbol}"
                    elif asset_cat == 'Futures':
                        symbol = symbol.replace(' ', '.')
                        symbol = f"F:{symbol}"
                    else:
                        symbol = symbol.replace(' ', '.')

                    # Remove common known exchange extensions to avoid doubling
                    symbol = re.sub(r'\.(TO|US|AX|L)$', '', symbol, flags=re.IGNORECASE)
                    
                    ext = _ib_currency_ext(currency)
                    full_symbol = f"{symbol}.{ext}"

                    transactions.append({
                        'action': action,
                        'date': date,
                        'time': time,
                        'date_settle': date_settle,
                        'symbol': full_symbol,
                        'quantity': qty,
                        'currency': currency,
                        'price': price,
                        'fee': comm_fee,
                        'net_amount': net_amount,
                        'gross_amount': gross_proceeds,
                        'account': 'IB',
                        'description': description,
                    })
                    self.note_row_consumed()

                elif section == 'Dividends':
                    # Strict header lookups (like Trades): a missing column
                    # previously fell back to row[0] — the literal section
                    # name 'Dividends' — via `.get(key, 0)`, emitting a junk
                    # transaction with currency/date='Dividends' instead of
                    # skipping. A missing column now raises KeyError and the
                    # row is skipped loudly.
                    try:
                        currency = row[header_map['Currency']]
                        date = row[header_map['Date']]
                        description = row[header_map['Description']]
                    except (KeyError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    # IB emits per-currency subtotal rows ('Total' in the
                    # currency or description cell) — skip before parsing.
                    if 'Total' in currency or 'Total' in description:
                        self.count_nonevent(f"{section} subtotal row")
                        continue

                    try:
                        # SIGN-PRESERVING: IB posts re-characterizations as a
                        # negative reversal row plus a corrected positive row.
                        # abs() here used to book all three legs as income
                        # (+250 / −250 / +240 → 740 instead of 240); keeping
                        # the sign lets the reversal net out downstream.
                        amount = float(row[header_map['Amount']].replace(',', ''))
                    except (KeyError, ValueError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue

                    # Ticker extraction logic from ib_dividends.pl
                    ticker = 'UNKNOWN'
                    isin = ''
                    # Try to find Ticker (ISIN) format
                    match = re.search(r'^([A-Z.\d\-]+(?:\s+[A-Z.\d\-]+)*)\s*\(([^)]+)\)', description)  # space-form class tickers ('BRK B') match; spaces dotted below
                    if match:
                        ticker, isin = match.groups()
                    else:
                        match = re.search(r'([A-Z.\d\-]+)', description)
                        if match: ticker = match.group(1)
                    
                    ticker = ticker.replace(' ', '.')
                    # Record (ticker, pay date) so the accrual diagnostic
                    # below can tell whether this dividend's cash has
                    # already been booked in this file.
                    posted_dividend_keys.add((ticker, date))
                    ext = 'US'
                    if isin:
                        isin_map = {'CA': 'TO', 'AU': 'AX', 'GB': 'L', 'IE': 'L', 'US': 'US'}
                        if len(isin) >= 2:
                            ext = isin_map.get(isin[:2].upper(), 'US')

                    # IB's PIL marker is "Payment in Lieu of Dividend" (note
                    # the lowercase 'in'); the previous match string had a
                    # capital 'I' and silently missed every PIL row. Match
                    # case-insensitively against both phrasings used by IB.
                    desc_lower = description.lower()
                    if is_roc_description(description):
                        # Return of capital arrives in the Dividends
                        # section but is an ACB reduction, not income.
                        # `amount` stays signed so IB's negative reversal
                        # rows net out (they become positive ADJUSTs).
                        transactions.append(self.tx_roc_adjust(
                            symbol=f"{ticker}.{ext}", currency=currency,
                            date=date, desc=description, amount=amount,
                            account='IB'))
                        self.note_row_consumed()
                        continue
                    is_pil = ('payment in lieu of dividend' in desc_lower
                              or 'in lieu of dividend' in desc_lower)
                    action = 'DIVIDEND_IN_LIEU' if is_pil else 'DIVIDEND'
                    # Type 'dividend' makes downstream tools skip ACB; PIL
                    # uses a different type so dividend gain-entry emission
                    # can exclude it cleanly (PIL is interest income, not
                    # an eligible/qualified dividend).
                    tx_type = 'dividend_in_lieu' if is_pil else 'dividend'

                    # IB dividend descriptions include "USD 0.24 per
                    # Share" — extract the per-share rate so the record
                    # reconciles against the user's pool size and T5/
                    # 1099-DIV. Qty is back-computed from amount/rate
                    # (IB ships gross on the Dividend row; withholding
                    # is in a separate section). Falls back to qty=
                    # price=0 when the pattern doesn't match.
                    qty, price = _parse_div_qty_rate(description, amount)
                    transactions.append({
                        'action': action,
                        'date': date,
                        'time': '09:30:00',
                        'date_settle': date,
                        'symbol': f"{ticker}.{ext}",
                        'quantity': qty,
                        'price': price,
                        'currency': currency,
                        'net_amount': amount,
                        'gross_amount': amount,
                        'type': tx_type,
                        'account': 'IB',
                        'description': description
                    })
                    self.note_row_consumed()

                elif section == 'Open Positions':
                    # Not emitted as transactions — read purely to seed the
                    # income-reattribution holdings map (see
                    # _reattribute_income_to_holdings). Summary rows only
                    # (Lot rows repeat the same symbol per acquisition).
                    try:
                        discr = row[header_map['DataDiscriminator']]
                        asset_cat = row[header_map['Asset Category']]
                        currency = row[header_map['Currency']]
                        symbol = row[header_map['Symbol']]
                        qty_raw = row[header_map['Quantity']]
                    except (KeyError, IndexError):
                        self.count_skip(f"malformed {section} row")
                        continue
                    if discr != 'Summary' or asset_cat != 'Stocks':
                        # Lot rows, option/futures summaries, subtotals:
                        # nothing the holdings seed needs.
                        self.count_nonevent(f"{section} row (not a stock "
                                            f"summary)")
                        continue
                    try:
                        if abs(float(qty_raw.replace(',', ''))) < 1e-9:
                            self.count_nonevent(f"{section} zero-quantity "
                                                f"row")
                            continue
                    except ValueError:
                        self.count_skip(f"malformed {section} row")
                        continue
                    sym = symbol.strip().replace(' ', '.')
                    sym = re.sub(r'\.(TO|US|AX|L)$', '', sym, flags=re.IGNORECASE)
                    open_position_syms.add(f"{sym}.{_ib_currency_ext(currency)}")
                    self.note_row_consumed()      # read into parser state

                elif section == 'Statement':
                    # Capture the statement's period-end date so the
                    # accrual diagnostic can tell a payable-now dividend
                    # from a normal future-dated pending accrual.
                    fn_idx = header_map.get('Field Name')
                    fv_idx = header_map.get('Field Value')
                    if (fn_idx is not None and fv_idx is not None
                            and fv_idx < len(row)
                            and row[fn_idx] == 'Period'):
                        raw = row[fv_idx]
                        end_part = raw.split(' - ')[-1].strip()
                        try:
                            statement_period_end = datetime.strptime(
                                end_part, "%B %d, %Y").strftime("%Y-%m-%d")
                        except ValueError:
                            statement_period_end = ''
                        self.note_row_consumed()  # read into parser state
                    else:
                        self.count_nonevent("Statement metadata row")

                elif section == 'Change in Dividend Accruals':
                    # Net 'Po' (accrual posted) against 'Re' (reversed) per
                    # dividend. Keyed on (account, symbol, ex date, pay
                    # date) — a Po/Re pair for the same dividend shares all
                    # four and nets to zero; an un-reversed Po stays
                    # positive. Not emitted as a transaction; only feeds
                    # the end-of-parse diagnostic.
                    code = row[header_map.get('Code', 0)] if 'Code' in header_map else ''
                    if code not in ('Po', 'Re'):
                        self.count_nonevent(f"{section} row without a "
                                            f"Po/Re code (subtotal)")
                        continue
                    symbol = (row[header_map['Symbol']]
                              if 'Symbol' in header_map
                              and header_map['Symbol'] < len(row) else '')
                    if not symbol:
                        self.count_nonevent(f"{section} row without a "
                                            f"symbol (subtotal)")
                        continue
                    symbol = symbol.replace(' ', '.')
                    account = (row[header_map['Account']]
                               if 'Account' in header_map
                               and header_map['Account'] < len(row) else '')
                    ex_date = (row[header_map['Ex Date']]
                               if 'Ex Date' in header_map
                               and header_map['Ex Date'] < len(row) else '')
                    pay_date = (row[header_map['Pay Date']]
                                if 'Pay Date' in header_map
                                and header_map['Pay Date'] < len(row) else '')
                    currency = (row[header_map['Currency']]
                                if 'Currency' in header_map
                                and header_map['Currency'] < len(row) else '')
                    gross_idx = header_map.get('Gross Amount')
                    if gross_idx is None or gross_idx >= len(row):
                        self.count_skip(f"malformed {section} row")
                        continue
                    try:
                        gross = float(row[gross_idx].replace(',', ''))
                    except (ValueError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    key = (account, symbol, ex_date, pay_date)
                    accrual_net[key] = accrual_net.get(key, 0.0) + gross
                    self.note_row_consumed()      # read into parser state
                    accrual_meta[key] = {
                        'symbol': symbol, 'pay_date': pay_date,
                        'currency': currency,
                    }
                    # Capture the per-share rate (same for the Po and Re
                    # rows of a dividend) keyed by (symbol, pay date), so a
                    # Payment-in-Lieu row — which has no rate in its own
                    # description — can be reconciled to shares below.
                    rate_idx = header_map.get('Gross Rate')
                    if rate_idx is not None and rate_idx < len(row):
                        try:
                            r = float(row[rate_idx].replace(',', ''))
                        except (ValueError, IndexError):
                            r = 0.0
                        if r and (symbol, pay_date) not in accrual_rate:
                            accrual_rate[(symbol, pay_date)] = r

                elif section == 'Withholding Tax':
                    # Strict header lookups — see the Dividends section. A
                    # missing column used to fall back to row[0] ('Withholding
                    # Tax') instead of skipping the row.
                    try:
                        currency = row[header_map['Currency']]
                        date = row[header_map['Date']]
                        description = row[header_map['Description']]
                    except (KeyError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    if 'Total' in currency:
                        self.count_nonevent(f"{section} subtotal row")
                        continue
                    try:
                        # IB books withholding as a NEGATIVE amount (cash out)
                        # and a refund/correction as POSITIVE. The repo-wide
                        # TAX convention is positive = tax withheld (RBC emits
                        # it that way), so flip the sign rather than abs() it:
                        # a charge stays positive and a refund nets NEGATIVE
                        # instead of double-counting as more tax paid.
                        amount = -float(row[header_map['Amount']].replace(',', ''))
                    except (KeyError, ValueError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue

                    # Description shape:  "AAPL (US0378331005) Cash Dividend..."
                    # — pull the ticker AND the ISIN so the market suffix
                    # comes from the security's country (same isin_map the
                    # dividend handler uses above) rather than hardcoding
                    # `.US`. Hardcoded `.US` fragmented the TAX symbol
                    # from non-US dividends on the same security, so the
                    # foreign-tax-credit pairing broke for any non-US
                    # holding.
                    ticker = 'UNKNOWN'
                    isin = ''
                    m = re.search(r'^([A-Z.\d\-]+(?:\s+[A-Z.\d\-]+)*)\s*\(([^)]+)\)', description)  # space-form class tickers ('BRK B') match; spaces dotted below
                    if m:
                        ticker, isin = m.groups()
                    else:
                        m = re.search(r'([A-Z.\d\-]+)', description)
                        if m: ticker = m.group(1)
                    ticker = ticker.replace(' ', '.')

                    ext = 'US'
                    if isin:
                        isin_map = {'CA': 'TO', 'AU': 'AX', 'GB': 'L', 'IE': 'L', 'US': 'US'}
                        if len(isin) >= 2:
                            ext = isin_map.get(isin[:2].upper(), 'US')

                    transactions.append({
                        'action': 'TAX',
                        'date': date,
                        'time': '09:30:00',
                        'date_settle': date,
                        'symbol': f"{ticker}.{ext}",
                        'quantity': 0.0,
                        'currency': currency,
                        'net_amount': amount,
                        'type': 'tax',
                        'account': 'IB',
                        'description': description
                    })
                    self.note_row_consumed()

                elif section == 'Interest':
                    # Strict lookups (like Trades/Dividends): a missing
                    # column must skip loudly, not fall back to row[0] (the
                    # literal section name) as currency/date.
                    try:
                        currency = row[header_map['Currency']]
                        date = row[header_map['Date']]
                        description = row[header_map['Description']]
                    except (KeyError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    if 'Total' in currency:
                        self.count_nonevent(f"{section} subtotal row")
                        continue
                    try:
                        amount = float(row[header_map['Amount']].replace(',', ''))
                    except (KeyError, ValueError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue

                    transactions.append({
                        'action': 'INTEREST',
                        'date': date,
                        'time': '09:30:00',
                        'date_settle': date,
                        'symbol': 'CASH',
                        'quantity': 0.0,
                        'currency': currency,
                        'net_amount': amount, # Preserve sign for interest paid vs charged
                        'type': 'interest',
                        'account': 'IB',
                        'description': description
                    })
                    self.note_row_consumed()

                elif section == 'Fees':
                    # Strict lookups: a missing column skips loudly instead
                    # of reading row[0] (the literal section name) as the
                    # currency/date (see the Trades/Dividends fix).
                    try:
                        currency = row[header_map['Currency']]
                        description = row[header_map['Description']]
                        date = row[header_map['Date']]
                    except (KeyError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    # Subtotal rows: 'Total' / 'Total in CAD' in the
                    # Currency cell, OR a blank Currency with 'Total' in
                    # the Subtitle/Description. The blank-currency form
                    # used to fall through (defaulted to CAD, no date,
                    # no 'for Mmm YYYY' hint) into the dateless-fees
                    # warning, which then reported the subtotal ON TOP
                    # of the rows it summed — a doubled total.
                    _subtitle = (row[header_map['Subtitle']]
                                 if 'Subtitle' in header_map
                                 and header_map['Subtitle'] < len(row)
                                 else '')
                    if ('Total' in currency or not currency
                            or 'Total' in _subtitle
                            or 'Total' in description):
                        self.count_nonevent(f"{section} subtotal row")
                        continue

                    try:
                        # IB books a charge NEGATIVE (cash out) and a
                        # refund/reversal POSITIVE. Repo FEE convention
                        # (taxjson_fx_cash, .tt round-trip): positive =
                        # charged — flip, like the Withholding Tax
                        # branch does for TAX, so a market-data charge
                        # is an outflow and a reversal nets against it.
                        amount = -float(row[header_map['Amount']].replace(',', ''))
                    except (KeyError, ValueError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue

                    if not date:
                        # Extract date from description if possible (e.g., "for Feb 2025")
                        date_match = re.search(r'for\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', description, re.IGNORECASE)
                        if date_match:
                            mon, yr = date_match.groups()
                            mon_map = {
                                'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                                'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
                            }
                            date = f"{yr}-{mon_map.get(mon.upper())}-01"
                        else:
                            # No date column AND no "for Mmm YYYY" hint. The
                            # old fallback stamped a literal "2025-01-01"
                            # which silently mis-files the row in any other
                            # tax year. Skip + accumulate; a single summary
                            # line fires at the end of parse_file.
                            skipped_dateless_fees.append(amount)
                            self.count_skip(f"{section} row with no date "
                                            f"(see warning)")
                            continue

                    transactions.append({
                        'action': 'FEE',
                        'date': date,
                        'time': '09:30:00',
                        'date_settle': date,
                        'symbol': 'CASH',
                        'quantity': 0.0,
                        'currency': currency,
                        'net_amount': amount,
                        'type': 'fee',
                        'account': 'IB',
                        'description': description
                    })
                    self.note_row_consumed()

                elif section == 'Transaction Fees':
                    # Per-trade levies IB books OUTSIDE the trade's
                    # Comm/Fee column (UK Stamp Tax, FINRA/SEC-style
                    # transaction charges): folded into the same-day
                    # BUYSELL on the symbol after the whole file is
                    # read (see pending_txn_fees). Header:
                    #   Asset Category,Currency,Account,Date/Time,
                    #   Symbol,Description,Quantity,Trade Price,
                    #   Amount,Code
                    try:
                        asset_cat = row[header_map['Asset Category']]
                        currency = row[header_map['Currency']]
                        date_time_str = row[header_map['Date/Time']]
                        symbol = row[header_map['Symbol']]
                        description = row[header_map['Description']]
                        amount_str = row[header_map['Amount']].replace(',', '')
                        qty_str = (row[header_map['Quantity']]
                                   .replace(',', '')
                                   if 'Quantity' in header_map
                                   and header_map['Quantity'] < len(row)
                                   else '')
                    except (KeyError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    if (not currency or 'Total' in currency
                            or asset_cat.startswith('Total')):
                        self.count_nonevent(f"{section} subtotal row")
                        continue
                    try:
                        amount = float(amount_str or 0)
                        qty = float(qty_str or 0)
                    except ValueError as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    parts = date_time_str.replace(',', '').split()
                    date = parts[0] if parts else ''
                    time = parts[1] if len(parts) > 1 else '09:30:00'
                    if not date or not symbol.strip():
                        self.count_skip(f"{section} row with no date/symbol")
                        continue
                    sym = symbol.strip().replace(' ', '.')
                    sym = re.sub(r'\.(TO|US|AX|L)$', '', sym, flags=re.IGNORECASE)
                    pending_txn_fees.append({
                        'date': date, 'time': time,
                        'symbol': f"{sym}.{_ib_currency_ext(currency)}",
                        'quantity': qty,
                        # IB books the levy negative (cash out).
                        'amount': abs(amount),
                        'currency': currency,
                        'description': (description.strip()
                                        or 'Transaction fee'),
                    })
                    self.note_row_consumed()

                elif section == 'Commission Adjustments':
                    # Post-trade commission corrections, e.g.
                    #   USD,2025-02-10,"Refund (KWEB, -200 2025-02-07)",1.25
                    # IB's Amount is signed cash (a refund POSITIVE).
                    # Emitted as a FEE with the repo sign (positive =
                    # charged) so a refund is a NEGATIVE fee that nets
                    # against the original commission in fee totals.
                    # Bound to the ticker named in the description
                    # (the fee report groups by symbol); CASH when the
                    # description names none.
                    try:
                        currency = row[header_map['Currency']]
                        date = row[header_map['Date']]
                        description = row[header_map['Description']]
                        amount_str = row[header_map['Amount']].replace(',', '')
                    except (KeyError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    if not currency or 'Total' in currency:
                        self.count_nonevent(f"{section} subtotal row")
                        continue
                    try:
                        amount = float(amount_str or 0)
                    except ValueError as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    if not date:
                        self.count_skip(f"{section} row with no date")
                        continue
                    _m = _IB_COMM_ADJ_TICKER_RE.search(description or '')
                    if _m:
                        _tk = _m.group(1).strip().replace(' ', '.')
                        _tk = re.sub(r'\.(TO|US|AX|L)$', '', _tk,
                                     flags=re.IGNORECASE)
                        symbol = f"{_tk}.{_ib_currency_ext(currency)}"
                    else:
                        symbol = 'CASH'
                    transactions.append({
                        'action': 'FEE',
                        'date': date,
                        'time': '09:30:00',
                        'date_settle': date,
                        'symbol': symbol,
                        'quantity': 0.0,
                        'currency': currency,
                        'net_amount': -amount,
                        'type': 'fee',
                        'account': 'IB',
                        'description': f"{description} (Commission "
                                       f"Adjustments)",
                    })
                    self.note_row_consumed()

                elif section == 'Corporate Actions':
                    # Strict lookups (see Fees/Interest above).
                    try:
                        currency = row[header_map['Currency']]
                        date_time_str = row[header_map['Date/Time']]
                        description = row[header_map['Description']]
                    except (KeyError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    # Subtotal rows (Asset Category 'Total', blank
                    # Currency) must be recognized BEFORE the currency
                    # → suffix lookup: the blank currency used to reach
                    # _ib_currency_ext and fire the "IB currency '' has
                    # no exchange-suffix mapping" warning on every
                    # statement with a Corporate Actions total.
                    _ca_cat = (row[header_map['Asset Category']]
                               if 'Asset Category' in header_map
                               and header_map['Asset Category'] < len(row)
                               else '')
                    if (not currency or 'Total' in currency
                            or _ca_cat.startswith('Total')):
                        self.count_nonevent(f"{section} subtotal row")
                        continue

                    parts = date_time_str.replace(',', '').split()
                    date = parts[0] if parts else ''
                    time = parts[1] if len(parts) > 1 else '09:30:00'

                    # Same row-skip-on-bad-cell discipline as the Trades
                    # branch — a malformed Quantity / Value here used to
                    # crash the whole parse.
                    try:
                        qty_str = row[header_map['Quantity']].replace(',', '')
                        qty = float(qty_str) if qty_str else 0.0
                        val_str = row[header_map['Value']].replace(',', '')
                        val = float(val_str) if val_str else 0.0
                        # Cash actually paid (cash in lieu); older
                        # statement layouts lack the column.
                        proceeds_str = (row[header_map['Proceeds']]
                                        if 'Proceeds' in header_map
                                        and len(row) > header_map['Proceeds']
                                        else '').replace(',', '')
                        proceeds = float(proceeds_str) if proceeds_str else 0.0
                    except (KeyError, ValueError, IndexError) as e:
                        print(f"warning: skipping malformed IB Corporate Actions row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue

                    ext = _ib_currency_ext(currency)

                    # Cancel/rebook restatements: IB re-lists a
                    # corrected corporate action as original + `Ca`
                    # cancellation + rebooked rows. This branch never
                    # read the Code cell, so a restated spinoff
                    # emitted its income and share rows TWICE (the
                    # negative Ca leg only fed the "unhandled" note).
                    # A Ca spinoff leg CONSUMES its original's
                    # DIVIDEND+BUYSELL pair (leaving the rebooked,
                    # corrected rows); Ca rows never reach the split
                    # accumulators or the unhandled tally.
                    _cacode = (row[header_map['Code']]
                               if 'Code' in header_map
                               and len(row) > header_map['Code'] else '')
                    if 'Ca' in re.split(r'[;,\s]+', _cacode or ''):
                        _sm = re.search(r'Spinoff.*?\((\w+),',
                                        description, re.IGNORECASE)
                        if _sm and qty < 0:
                            _sym = f"{_sm.group(1).replace(' ', '.')}.{ext}"
                            for _i in range(len(transactions) - 1, -1, -1):
                                _t = transactions[_i]
                                if (_t.get('action') == 'BUYSELL'
                                        and _t.get('symbol') == _sym
                                        and _t.get('date') == date
                                        and abs(float(_t.get('quantity')
                                                      or 0) + qty) < 1e-9
                                        and _t.get('description')
                                        == description):
                                    del transactions[_i]
                                    break
                            for _i in range(len(transactions) - 1, -1, -1):
                                _t = transactions[_i]
                                if (_t.get('action') == 'DIVIDEND'
                                        and _t.get('symbol') == _sym
                                        and _t.get('date') == date
                                        and abs(float(_t.get('net_amount')
                                                      or 0)
                                                - abs(val)) < 0.01
                                        and _t.get('description')
                                        == description):
                                    del transactions[_i]
                                    break
                        self.note_row_consumed()  # consumed its original
                        continue

                    # Tender / voluntary-offer journals (see tender_legs
                    # above). A zero-proceeds leg only moves shares
                    # between the ticker and its `.TEN` placeholder —
                    # a recognized non-event, netted and noted at end
                    # of parse. A leg carrying cash on a negative
                    # quantity is the ACCEPTED cash tender: the shares
                    # are gone and the cash is proceeds — booked as a
                    # sale of the ROOT symbol (the placeholder never
                    # entered the book) with a loud NOTE, so the
                    # disposition can't vanish into the unhandled
                    # tally the way every other non-split/spinoff
                    # corporate action does.
                    _troot = ib_tender_root(description)
                    if _troot is not None:
                        _tsym = f"{_troot}.{ext}"
                        _tl = tender_legs.setdefault(_tsym, {
                            'rows': 0, 'parked': 0.0, 'cash_qty': 0.0,
                            'cash': 0.0, 'dates': set(), 'currency': currency})
                        _tl['rows'] += 1
                        _tl['dates'].add(date)
                        # Which leg touches the placeholder line is
                        # fixed by the row kind, not by the leading
                        # token (IB may reuse the root's description
                        # on both legs): a `Tendered to` row's POSITIVE
                        # leg moves shares INTO the placeholder, a
                        # `Voluntary Offer Allocation` row's NEGATIVE
                        # leg moves them OUT.
                        _tender_in = 'tendered to' in description.lower()
                        _is_placeholder = ((_tender_in and qty > 0)
                                           or (not _tender_in and qty < 0))
                        if abs(proceeds) < 0.005:
                            if _is_placeholder:
                                _tl['parked'] += qty
                            self.count_nonevent(
                                f"{section} tender/voluntary-offer share "
                                f"journal (zero proceeds)")
                            continue
                        if qty < 0:
                            _cash = abs(proceeds)
                            transactions.append({
                                'action': 'BUYSELL',
                                'date': date,
                                'time': time,
                                'date_settle': date,
                                'symbol': _tsym,
                                'quantity': qty,
                                'currency': currency,
                                'price': round(_cash / -qty, 8),
                                'fee': 0.0,
                                'net_amount': _cash,
                                'gross_amount': _cash,
                                'account': 'IB',
                                'description': description,
                            })
                            _tl['cash_qty'] += -qty
                            _tl['cash'] += _cash
                            if _is_placeholder:
                                _tl['parked'] += qty
                            self.note_row_consumed()
                            continue
                        # Cash on a POSITIVE leg is a shape we have not
                        # seen — loud bucket, never a silent drop.
                        self.count_skip(f"{section} tender row with "
                                        f"proceeds on a positive quantity")
                        continue

                    # Split 3 for 2. NOT gated on qty > 0: a reverse split's
                    # share-reduction leg arrives with NEGATIVE quantity (and
                    # some events ship ONLY that leg) — the old `qty > 0`
                    # gate silently dropped it, leaving the pool 10x too big.
                    # The ratio math (new/old from the description) is
                    # sign-independent; paired negative+positive legs are
                    # collapsed by the per-file (symbol, date, ratio) key.
                    handled = False

                    # Cash in lieu of the fractional share a ratio left
                    # over: a disposition of that fraction for the cash
                    # — the same shape the RBC parser's CIL rows take.
                    # Left unbooked, the fraction sat in the pool
                    # forever and the cash was never proceeds. Checked
                    # before the split branch so a CIL description
                    # that also names the split is not read as a leg.
                    cil_match = _IB_CIL_RE.search(description)
                    if cil_match and qty < 0:
                        ticker = cil_match.group(1).strip().replace(' ', '.')
                        symbol = f"{ticker}.{ext}"
                        cash = abs(proceeds) or abs(val)
                        transactions.append({
                            'action': 'BUYSELL',
                            'date': date,
                            'time': time,
                            'date_settle': date,
                            'symbol': symbol,
                            'quantity': qty,
                            'currency': currency,
                            'price': round(cash / -qty, 8),
                            'net_amount': cash,
                            'account': 'IB',
                            'description': description
                        })
                        # The fraction belongs to this symbol's latest
                        # split on or before this date; a CIL row filed
                        # ahead of its legs waits for them.
                        _cands = [i for (s, d, _r), i in emitted_splits.items()
                                  if s == symbol and d <= date]
                        if _cands:
                            _info = max(_cands, key=lambda i: i['tx']['date'])
                            _info['cil'] += -qty
                            _ib_refine_split_ratio(_info)
                        else:
                            pending_cil[symbol] = pending_cil.get(symbol, 0.0) - qty
                        handled = True

                    split_match = re.search(r'^([A-Z0-9\s\.]+)\s*\(([^)]+)\)\s+Split\s+([\d\.]+)\s+for\s+([\d\.]+)', description, re.IGNORECASE)
                    if split_match and not handled:
                        ticker = split_match.group(1).strip().replace(' ', '.')
                        new_sh = float(split_match.group(3))
                        old_sh = float(split_match.group(4))
                        ratio = new_sh / old_sh if old_sh != 0 else 1.0
                        symbol = f"{ticker}.{ext}"
                        split_key = (symbol, date, round(ratio, 9))
                        info = emitted_splits.get(split_key)
                        if info is None:
                            tx = {
                                'action': 'SPLIT',
                                'date': date,
                                'time': time,
                                'date_settle': date,
                                'symbol': symbol,
                                'symbol_new': symbol,
                                'quantity': ratio,
                                'currency': currency,
                                'account': 'IB',
                                'description': description
                            }
                            transactions.append(tx)
                            info = {'tx': tx, 'new': 0.0, 'old': 0.0,
                                    'text_ratio': ratio,
                                    'cil': pending_cil.pop(symbol, 0.0)}
                            emitted_splits[split_key] = info
                        if qty > 0:
                            info['new'] += qty
                        elif qty < 0:
                            info['old'] += -qty
                        _ib_refine_split_ratio(info)
                        handled = True

                    # Spinoff 1 for 10. Gated on `not handled` so a
                    # description that happens to contain both "Split"
                    # and "Spinoff" keywords doesn't emit duplicate
                    # transactions (the split branch above already
                    # consumed it).
                    spinoff_match = re.search(r'Spinoff.*?\((\w+),', description, re.IGNORECASE)
                    if spinoff_match and qty > 0 and not handled:
                        ticker = spinoff_match.group(1).replace(' ', '.')
                        price = round(abs(val / qty), 8) if qty != 0 else 0.0
                        symbol = f"{ticker}.{ext}"
                        
                        # Output DIVIDEND and BUYSELL for the new shares
                        transactions.append({
                            'action': 'DIVIDEND',
                            'date': date,
                            'time': time,
                            'date_settle': date,
                            'symbol': symbol,
                            'quantity': 0.0,
                            'currency': currency,
                            'net_amount': abs(val),
                            'gross_amount': abs(val),
                            'type': 'dividend',
                            'account': 'IB',
                            'description': description
                        })
                        transactions.append({
                            'action': 'BUYSELL',
                            'date': date,
                            'time': time,
                            'date_settle': date,
                            'symbol': symbol,
                            'quantity': qty,
                            'currency': currency,
                            'price': price,
                            'net_amount': abs(val),
                            'account': 'IB',
                            'description': description
                        })
                        handled = True

                    # Anything else (Merger/Acquisition, name change, etc.)
                    # — IB's row format varies by event type and matching
                    # paired rows safely is tricky. Track per ticker so we
                    # can warn the user once at end of parse. We pull a
                    # rough ticker out of the description's leading token.
                    if handled:
                        self.note_row_consumed()
                    elif qty != 0:
                        m = re.match(r'\s*([A-Z0-9\.]{1,12})', description)
                        ticker = m.group(1) if m else '<unknown>'
                        unhandled_ca_tickers[ticker] = unhandled_ca_tickers.get(ticker, 0) + 1
                        self.count_skip(f"{section} row not translated "
                                        f"(see NOTE)")
                    else:
                        self.count_nonevent(f"{section} zero-quantity row")

                elif section == 'Transfers':
                    # Strict lookups (see Fees/Interest above).
                    try:
                        asset_cat = row[header_map['Asset Category']]
                        transfer_type = row[header_map['Type']]
                        symbol = row[header_map['Symbol']]
                        currency = row[header_map['Currency']]
                        date = row[header_map['Date']]
                        qty_str = row[header_map['Qty']].replace(',', '')
                    except (KeyError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    if asset_cat not in ('Stocks', 'Equity and Index Options'):
                        # Cash movements / subtotals: custody of money,
                        # not property.
                        self.count_nonevent(f"{section} row (cash or "
                                            f"subtotal)")
                        continue
                    if not qty_str:
                        self.count_nonevent(f"{section} row with no "
                                            f"quantity")
                        continue
                    try:
                        qty = float(qty_str)
                    except ValueError as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue

                    # Skip cash transfers
                    if symbol.startswith('CASH.'):
                        self.count_nonevent(f"{section} cash transfer")
                        continue

                    try:
                        total_cost = float(row[header_map['Market Value']].replace(',', ''))
                    except (KeyError, ValueError, IndexError) as e:
                        print(f"warning: skipping malformed IB {section} row ({e}): {row}",
                              file=sys.stderr)
                        self.count_skip(f"malformed {section} row")
                        continue
                    
                    is_option = False
                    if asset_cat == 'Equity and Index Options':
                        is_option = True
                        opt_match = re.search(r'^([A-Z\d\.]+)\s+(\d{2})([A-Z]{3})(\d{2})\s+([\d\.]+)\s+([PC])$', symbol)
                        if opt_match:
                            base, day, mon, yr, strike, right = opt_match.groups()
                            mon_map = {
                                'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                                'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
                            }
                            month = mon_map.get(mon.upper())
                            if month:
                                symbol = f"{base}{yr}{month}{day}{right}{encode_occ_strike(strike)}"
                        symbol = symbol.replace(' ', '')
                    else:
                        symbol = symbol.replace(' ', '.')
                    
                    symbol = re.sub(r'\.(TO|US|AX|L)$', '', symbol, flags=re.IGNORECASE)
                    ext = _ib_currency_ext(currency)
                    symbol = f"{symbol}.{ext}"

                    abs_qty = abs(qty)
                    multiplier = 100 if is_option else 1
                    price = (round(abs(total_cost) / (abs_qty * multiplier), 8)
                             if abs_qty > 1e-6 else 0.0)

                    # Cancel/rebook: IB lists a reversed ACATS/ATON leg
                    # as original + `Ca` cancellation (opposite qty,
                    # same date) + rebooked rows. A real RRSP move
                    # (IB -> Questrade) carried MDA five times: Out,
                    # Ca, Out, Ca, Out — arithmetically -383, but the
                    # two Ca legs read as +383 ACQUISITIONS to the
                    # superficial-loss walk. The Ca row consumes its
                    # original; only when the original sits in an
                    # earlier statement does the reversal stay as a
                    # netting leg.
                    _xcode = (row[header_map['Code']]
                              if 'Code' in header_map
                              and len(row) > header_map['Code'] else '')
                    if 'Ca' in re.split(r'[;,\s]+', _xcode or ''):
                        for _i in range(len(transactions) - 1, -1, -1):
                            _t = transactions[_i]
                            if (_t.get('action') == 'TRANSFER'
                                    and _t.get('symbol') == symbol
                                    and _t.get('date') == date
                                    and abs(float(_t.get('quantity') or 0)
                                            + qty) < 1e-9
                                    and _t.get('description')
                                    == transfer_type):
                                del transactions[_i]
                                self.note_row_consumed()  # + original
                                break
                        else:
                            print(f"note: {symbol}: IB cancelled a "
                                  f"{transfer_type} transfer of {-qty:g} "
                                  f"on {date} whose original row is not "
                                  f"in this statement — kept as a "
                                  f"reversing TRANSFER leg.",
                                  file=sys.stderr)
                            transactions.append({
                                'action': 'TRANSFER', 'date': date,
                                'time': '09:30:00', 'date_settle': date,
                                'symbol': symbol, 'quantity': qty,
                                'currency': currency, 'price': price,
                                'net_amount': abs(total_cost),
                                'account': 'IB',
                                'description': f"{transfer_type} (Ca)",
                            })
                            self.note_row_consumed()
                        continue

                    transactions.append({
                        'action': 'TRANSFER',
                        'date': date,
                        'time': '09:30:00',
                        'date_settle': date,
                        'symbol': symbol,
                        'quantity': qty,
                        'currency': currency,
                        'price': price,
                        'net_amount': abs(total_cost),
                        'account': 'IB',
                        'description': transfer_type
                    })
                    self.note_row_consumed()

                else:
                    # Sections the parser has no branch for (Account
                    # Information, Net Asset Value, Cash Report, Mark-
                    # to-Market, Financial Instrument Information, ...)
                    # are statement metadata / roll-ups, not events:
                    # counted under the calmer note, never dropped
                    # unaccounted.
                    self.count_nonevent(f"section {section} (not "
                                        f"translated)")

        # Fold each Transaction Fees levy into the same-day BUYSELL on
        # its symbol (exact-quantity match first, then any same-day
        # trade, each trade absorbing at most one levy). A levy with no
        # trade to join — the trade sits in another statement, or the
        # symbol differs — becomes a symbol-bound FEE row instead: a
        # standalone FEE never folds into ACB, but it is at least
        # visible in fee totals rather than lost.
        # IB levies the fee PER FILL while the Trades section carries
        # one Order row: a 10,000-share buy filled 8,900 + 1,100 has two
        # UK Stamp Tax rows. Each trade keeps a running "unlevied"
        # quantity so several fee rows can fold into it (an exact
        # match on the remaining or full quantity wins, else the trade
        # with the most room); a taken-once set sent the second row
        # out as a standalone FEE that never reached the ACB.
        _fee_room: Dict[int, float] = {}
        for _pf in pending_txn_fees:
            _cands = [t for t in transactions
                      if t.get('action') == 'BUYSELL'
                      and t.get('symbol') == _pf['symbol']
                      and t.get('date') == _pf['date']]
            for t in _cands:
                _fee_room.setdefault(id(t), abs(float(t.get('quantity')
                                                      or 0)))
            _fq = abs(_pf['quantity'])
            _open = [t for t in _cands if _fee_room[id(t)] > 1e-9]
            _exact = [t for t in _open
                      if abs(_fee_room[id(t)] - _fq) < 1e-9
                      or abs(abs(float(t.get('quantity') or 0)) - _fq)
                      < 1e-9]
            _fit = sorted((t for t in _open if _fee_room[id(t)] >= _fq),
                          key=lambda t: _fee_room[id(t)])
            _tgt = (_exact or _fit or _open or [None])[0]
            if _tgt is not None:
                _fee_room[id(_tgt)] = max(0.0, _fee_room[id(_tgt)] - _fq)
                _amt = _pf['amount']
                _q = float(_tgt.get('quantity') or 0)
                _tgt['fee'] = round(float(_tgt.get('fee') or 0) + _amt, 8)
                # Engine convention: net_amount is fee-inclusive —
                # cost on a buy, fee-net proceeds on a sell.
                _tgt['net_amount'] = round(
                    float(_tgt.get('net_amount') or 0)
                    + (_amt if _q > 0 else -_amt), 8)
            else:
                transactions.append({
                    'action': 'FEE',
                    'date': _pf['date'],
                    'time': _pf['time'],
                    'date_settle': _pf['date'],
                    'symbol': _pf['symbol'],
                    'quantity': 0.0,
                    'currency': _pf['currency'],
                    'net_amount': _pf['amount'],
                    'type': 'fee',
                    'account': 'IB',
                    'description': f"{_pf['description']} (Transaction "
                                   f"Fees — no same-day trade to fold "
                                   f"into)",
                })

        for _tsym, _tl in sorted(tender_legs.items()):
            _dates = ', '.join(sorted(_tl['dates']))
            if _tl['cash_qty'] > 0:
                print(
                    f"NOTE: {_tsym}: tender/voluntary offer settled for "
                    f"cash — {_tl['cash_qty']:g} share(s) disposed for "
                    f"{_tl['cash']:.2f} {_tl['currency']} ({_dates}); "
                    f"booked as a sale. If the offer was share-for-share "
                    f"(new shares received), replace the sale with a "
                    f"taxjson-corp-actions election instead.",
                    file=sys.stderr)
            if abs(_tl['parked']) > 1e-9:
                print(
                    f"warning: {_tsym}: {_tl['parked']:g} share(s) still "
                    f"sit on the tender placeholder line at period end "
                    f"({_dates}) — the offer's outcome (shares returned "
                    f"or cash paid) is in a later statement; nothing was "
                    f"booked for them here.", file=sys.stderr)
            elif _tl['cash_qty'] == 0:
                print(
                    f"note: {_tsym}: {_tl['rows']} tender/voluntary-offer "
                    f"journal row(s) ({_dates}) moved shares to and from "
                    f"the tender placeholder with no cash — recognized "
                    f"no-op, nothing booked.", file=sys.stderr)

        # Back-fill quantity/price for dividend rows that had no per-share rate
        # in their own description — chiefly Payment-in-Lieu rows, which IB
        # ships as just "…Payment in Lieu of Dividend" with no rate or share
        # count. The rate lives in the Change in Dividend Accruals section
        # (same dividend, keyed by symbol + pay date), so shares = amount /
        # rate — reconciling a PIL exactly like a normal dividend. Only qty and
        # price are set; net_amount (the income) is untouched.
        for tx in transactions:
            if tx.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU'):
                continue
            if tx.get('price'):          # description already gave a rate
                continue
            ticker = (tx.get('symbol') or '').rsplit('.', 1)[0]
            rate = accrual_rate.get((ticker, tx.get('date')))
            # SIGNED amount: a reversal row back-computes a NEGATIVE share
            # count, matching _parse_div_qty_rate's convention (qty carries
            # the row's sign; the positive per-share rate stays positive).
            amt = float(tx.get('net_amount') or 0.0)
            if rate and rate > 0 and amt:
                tx['price'] = rate
                q = round(amt / rate, 8)
                # Same cent-rounding snap as _parse_div_qty_rate: the
                # paid amount is rounded to cents, so the division
                # lands near — not on — the true share count.
                nearest = round(q)
                if nearest != 0 and abs(abs(amt) - abs(nearest) * rate) <= 0.005 + 1e-9:
                    q = float(nearest)
                tx['quantity'] = q

        if unhandled_ca_tickers:
            total = sum(unhandled_ca_tickers.values())
            tickers = ', '.join(sorted(unhandled_ca_tickers.keys()))
            print(
                f"NOTE: {total} unhandled Corporate Action row(s) in {path.name} "
                f"for: {tickers}. Only SPLIT and Spinoff rows are auto-translated. "
                f"For mergers, name changes, or other events that affect basis, "
                f"add a manual TRANSFER entry to your *_in.tt file.",
                file=sys.stderr,
            )

        if skipped_dateless_fees:
            total = sum(skipped_dateless_fees)
            print(
                f"warning: skipped {len(skipped_dateless_fees)} IB Fees row(s) "
                f"with no date and no 'for Mmm YYYY' hint (total: {total:.2f}). "
                f"Edit the source CSV to add dates if these should appear in "
                f"FEE totals.",
                file=sys.stderr,
            )

        # Open accruals: a positive net (Po not yet reversed) whose cash
        # dividend isn't already posted in this file's Dividends section,
        # and whose pay date falls on or before the statement period end
        # (a future-dated accrual is a normal pending dividend, not a
        # missed one). When the period end is unknown, don't filter on it.
        open_accruals = []
        for key, net in accrual_net.items():
            if net <= 0.01:
                continue
            meta = accrual_meta[key]
            if (meta['symbol'], meta['pay_date']) in posted_dividend_keys:
                continue
            if (statement_period_end and meta['pay_date']
                    and meta['pay_date'] > statement_period_end):
                continue
            open_accruals.append(
                (meta['symbol'], meta['pay_date'], net, meta['currency']))
        if open_accruals:
            open_accruals.sort()
            details = '; '.join(
                f"{sym} pay:{pay or '?'} ~{amt:.2f} {cur}".rstrip()
                for sym, pay, amt, cur in open_accruals
            )
            print(
                f"warning: {len(open_accruals)} dividend(s) in {path.name} are "
                f"accrued but not yet booked as posted dividends ({details}). "
                f"IB books the cash with a lag — accruals are estimates and "
                f"are NOT counted as income. If a more recent statement "
                f"doesn't already show these as posted dividends, re-download "
                f"it before filing so the income is captured.",
                file=sys.stderr,
            )

        # Bind each dividend/withholding-tax row to the listing actually held
        # for its ticker (fixes ISIN-country vs listing-exchange mismatches on
        # dual-listed names). Runs after the full file is parsed so it sees
        # every position regardless of section order.
        _reattribute_income_to_holdings(transactions,
                                        extra_held=open_position_syms)

        # Parser-level disambiguation so a downstream `taxjson-sort --dedup`
        # can't collapse byte-identical split-fill rows. IB occasionally
        # emits sub-second split fills that collide at our second-precision
        # `time` field.
        self.disambiguate_split_fills(transactions)
        # One note for recognized non-events (Forex conversions,
        # subtotals, metadata sections) and one for rows the parser
        # could not classify — the other parsers already do this; IB
        # counted but never reported.
        self.emit_skip_summary(path.name)
        return transactions
