import csv
import io
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

from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         _parse_div_qty_rate,
                                         encode_occ_strike,
                                         is_roc_description,
                                         parse_strict_number)
from taxjson.lib.corp_actions import ib_tender_root

# Statement sections that are statement METADATA or roll-ups of rows the
# parser reads elsewhere — never tax events of their own. Any section
# NOT in this list and without a parser branch is reported loudly (and
# is a parse ERROR when its header carries money-like columns): a
# renamed or localized money section ("Dividendes", "Transactions")
# used to be counted as a quiet non-event while its rows vanished.
_IB_METADATA_SECTIONS = frozenset({
    'Statement', 'Account Information', 'Account Summary',
    'Net Asset Value', 'Change in NAV', 'Mark-to-Market Performance Summary',
    'Realized & Unrealized Performance Summary',
    'Month & Year to Date Performance Summary',
    'Total P/L for Statement Period', 'Cash Report', 'Interest Accruals',
    'Codes', 'Notes/Legal Notes', 'Financial Instrument Information',
    'Deposits & Withdrawals', 'Forex Balances', 'Forex P/L Details',
    'Net Stock Position Summary', 'Base Currency Exchange Rate',
    'Location of Customer Assets, Positions and Money',
    'Stock Yield Enhancement Program Securities Lent',
    'Stock Yield Enhancement Program Securities Lent Activity',
    'Stock Yield Enhancement Program Securities Lent Interest Details',
    # A per-fill BREAKDOWN of levies IB already includes in the Trades
    # Comm/Fee column (see the Transaction Fees note in parse_file).
    'Transaction Fees',
})
# Header columns that mark a section as carrying money.
_IB_MONEY_COLUMNS = frozenset({
    'Amount', 'Proceeds', 'Comm/Fee', 'Net Amount', 'Gross Amount',
    'Market Value', 'Cash Amount', 'Notional Value', 'Value', 'Basis',
})

# Cash Report lines the parsed rows must reproduce, per currency, to the
# cent (0.02). Each is compared to the sum of what the parser booked for
# it (see `_ib_cash_booked`).
_IB_CASH_TOL = 0.02
_IB_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_IB_TIME_RE = re.compile(r'^\d{1,2}:\d{2}(?::\d{2})?$')
# FII option symbol, OCC-style with IB's padding: "DFDV  251121P00012500".
_IB_FII_OCC_RE = re.compile(r'^([A-Z0-9.]+)\s+(\d{6})([CP])(\d{8})$')
_MON_MAP = {
    'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05',
    'JUN': '06', 'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10',
    'NOV': '11', 'DEC': '12'}
# Statement titles that are NOT an Activity Statement but share its CSV
# shape (a Realized Summary lists closed lots, not the trades — parsing
# it as activity invents or drops events).
_IB_REFUSED_TITLE_RE = re.compile(
    r'summary|confirmation|performance|mtm|projected|tax', re.IGNORECASE)


def _mask_account(acct: str) -> str:
    """First two characters + *** — account numbers never reach logs."""
    a = (acct or '').strip()
    return (a[:2] + '***') if a else '?'


def _ib_split_datetime(raw: str, where: str):
    """(date, time) from IB's `YYYY-MM-DD, HH:MM:SS`; a date in any
    other shape (Flex `20250328;093000`, `03/28/2025`) is refused —
    it used to pass through verbatim into `date` and misfile the year."""
    parts = (raw or '').replace(',', ' ').split()
    date = parts[0] if parts else ''
    time = parts[1] if len(parts) > 1 else '09:30:00'
    if not _IB_DATE_RE.match(date) or not _IB_TIME_RE.match(time):
        raise BrokerageParseError(
            f"{where}: Date/Time {raw!r} is not IB's 'YYYY-MM-DD, "
            f"HH:MM:SS' — refusing to guess the date")
    if len(time.split(':')) == 2:
        time += ':00'
    return date, time


def _ib_require_date(raw: str, where: str, field: str = 'Date') -> str:
    d = (raw or '').strip()
    if not _IB_DATE_RE.match(d):
        raise BrokerageParseError(
            f"{where}: {field} {raw!r} is not YYYY-MM-DD — refusing to "
            f"guess the date")
    return d


def _canonical_root(roots) -> str:
    """The canonical option root among aliases IB lists for ONE conid.
    OCC renames an adjusted contract by appending a digit (DFDV ->
    DFDV1), so the root every other alias extends is the original;
    otherwise the shortest (then alphabetical) wins."""
    roots = sorted(set(roots), key=lambda r: (len(r), r))
    for r in roots:
        if all(o == r or o.startswith(r) for o in roots):
            return r
    return roots[0]


def _ib_prescan(rows, where: str) -> Dict[str, Any]:
    """Read the statement-level context every row branch needs BEFORE
    the row walk (these sections sit after Trades in IB's layout):
    the title, the Financial Instrument Information multipliers /
    option root aliases / real expiries, the account ids, and the Cash
    Report totals."""
    hm: Dict[str, Dict[str, int]] = {}
    out: Dict[str, Any] = {
        'title': '', 'fii': {}, 'root_alias': {}, 'alias_conids': {},
        'accounts': set(), 'accounts_included': '', 'cash': {},
        'cash_currencies': set(), 'has_cash_report': False,
        'has_order_level': False,
    }
    occ_by_conid: Dict[str, set] = {}
    contract_conids: Dict[tuple, set] = {}
    for row in rows:
        if len(row) < 2:
            continue
        sec, kind = row[0], row[1]
        if kind == 'Header':
            hm[sec] = {c: i for i, c in enumerate(row)}
            continue
        if kind != 'Data' or sec not in hm:
            continue
        h = hm[sec]

        def g(col, _row=row, _h=h):
            i = _h.get(col)
            return _row[i].strip() if i is not None and i < len(_row) else ''

        if sec == 'Trades' and g('DataDiscriminator') == 'Order':
            out['has_order_level'] = True
        acct = g('Account')
        if acct and 'Total' not in acct:
            out['accounts'].add(acct)
        if sec == 'Statement' and g('Field Name') == 'Title':
            out['title'] = g('Field Value')
        elif sec == 'Account Information':
            fn = g('Field Name')
            if fn == 'Account':
                v = g('Field Value').split()[0] if g('Field Value') else ''
                if v:
                    out.setdefault('account_field', set()).add(v)
            elif fn == 'Accounts Included':
                out['accounts_included'] = g('Field Value')
                for a in g('Field Value').split(','):
                    if a.strip():
                        out['accounts'].add(a.strip())
        elif sec == 'Financial Instrument Information':
            cat = g('Asset Category')
            mult_raw = g('Multiplier')
            try:
                mult = (parse_strict_number(mult_raw, field='Multiplier',
                                            where=where)
                        if mult_raw else None)
            except BrokerageParseError:
                mult = None
            info = {'mult': mult, 'expiry': g('Expiry'),
                    'conid': g('Conid')}
            texts = [s.strip() for s in g('Symbol').split(',')
                     if s.strip()]
            if g('Description'):
                texts.append(g('Description'))
            for t in texts:
                out['fii'].setdefault((cat, t), info)
                out['fii'].setdefault((cat, re.sub(r'\s+', ' ', t)), info)
            if cat == 'Equity and Index Options':
                for t in texts:
                    m = _IB_FII_OCC_RE.match(t)
                    if m:
                        occ_by_conid.setdefault(info['conid'], set()).add(
                            m.groups())
                        contract_conids.setdefault(
                            m.groups()[1:], {}).setdefault(
                            m.group(1), set()).add(info['conid'])
        elif sec == 'Cash Report':
            out['has_cash_report'] = True
            cur = g('Currency')
            line = g('Currency Summary')
            if not cur or cur == 'Base Currency Summary' or not line:
                continue
            out['cash_currencies'].add(cur)
            try:
                tot = parse_strict_number(g('Total'), field='Total',
                                          where=f"{where}: Cash Report "
                                                f"{line} {cur}",
                                          allow_blank=True, blank=0.0)
            except BrokerageParseError:
                continue
            out['cash'][(line, cur)] = out['cash'].get((line, cur),
                                                       0.0) + tot
    # One conid listed under several option roots (DFDV 251121P...,
    # DFDV1 251121P... after a corporate action renamed the adjusted
    # contract): every alias root maps to the canonical one.
    for conid, occs in occ_by_conid.items():
        roots = {o[0] for o in occs}
        if len(roots) < 2:
            continue
        canon = _canonical_root(roots)
        for r in roots:
            if r != canon:
                out['root_alias'][r] = canon
                out['alias_conids'].setdefault(r, set()).add(conid)
    out['contract_conids'] = contract_conids
    # A consolidated statement names its members in "Accounts
    # Included"; a single-account one only in "Account".
    if not out['accounts_included']:
        out['accounts'] |= out.get('account_field', set())
    return out

# `Commission Adjustments` description: `Refund (KWEB, -200 2025-02-07)`
# — the ticker sits first inside the parenthetical.
_IB_COMM_ADJ_TICKER_RE = re.compile(r'\(\s*([A-Z0-9][A-Z0-9 .\-]*?)\s*,')


class IbBrokerage(BaseBrokerage):
    # How an IB "(Return of Capital)" distribution from a NON-Canadian
    # issuer (ISIN country != CA) is booked: "dividend" (default — ITA
    # s.90(2) deems a non-resident corporation's pro-rata distribution a
    # dividend) or "acb" (the earlier ACB-reduction treatment). Set by
    # taxjson-brokerage --foreign-roc, which `taxjson run` passes from
    # [settings] foreign_return_of_capital.
    foreign_return_of_capital = 'dividend'

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _read_rows(path: Path) -> List[List[str]]:
        """All CSV rows. utf-8-sig handles a BOM gracefully — IB's web
        export occasionally ships one and plain utf-8 reads it as a data
        byte, breaking the very first section-tag match; a UTF-16 BOM
        (a spreadsheet's "Unicode text" save) is decoded instead of
        failing on byte 0xFF."""
        raw = Path(path).read_bytes()
        if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
            text = raw.decode('utf-16')
        else:
            text = raw.decode('utf-8-sig')
        return list(csv.reader(io.StringIO(text, newline='')))

    @staticmethod
    def _cell(row: List[str], header_map: Dict[str, int], col: str) -> str:
        i = header_map.get(col)
        return row[i].strip() if i is not None and i < len(row) else ''

    @staticmethod
    def _check_statement_kind(pre: Dict[str, Any], path: Path) -> None:
        """Refuse an IB report that shares the Activity Statement's CSV
        shape but not its meaning: a Realized Summary lists realized
        LOTS, so reading it as activity books phantom trades and none of
        the income."""
        title = (pre.get('title') or '').strip()
        if not title or 'activity' in title.lower():
            return
        if _IB_REFUSED_TITLE_RE.search(title):
            raise BrokerageParseError(
                f"{path.name}: this is an IB {title!r} report, not an "
                f"Activity Statement — refusing to read it as trades and "
                f"income. Download Reports > Statements > Activity (CSV) "
                f"instead.")
        print(f"warning: {path.name}: IB statement title {title!r} is not "
              f"'Activity Statement' — parsing it as one; check the "
              f"result.", file=sys.stderr)

    @staticmethod
    def _multiplier(asset_cat: str, raw_symbol: str, fii: Dict[tuple, Any],
                    where: str) -> float:
        """Contract size for a row: IB's own Financial Instrument
        Information Multiplier when the statement lists the instrument
        (CL 1000, ES 50, MET/MBT 0.1, SI 5000, GC 100, equity option
        100); otherwise 1 per share/warrant and 100 per equity option.
        A futures / futures-option contract size cannot be guessed, so
        its absence fails the parse."""
        s = (raw_symbol or '').strip()
        info = (fii.get((asset_cat, s))
                or fii.get((asset_cat, re.sub(r'\s+', ' ', s))))
        if info and info.get('mult'):
            return float(info['mult'])
        if asset_cat in ('Stocks', 'Warrants'):
            return 1.0
        if asset_cat == 'Equity and Index Options':
            return 100.0
        raise BrokerageParseError(
            f"{where}: {asset_cat} {s!r} has no multiplier in the "
            f"statement's Financial Instrument Information section — a "
            f"futures contract size (CL 1000, ES 50, MET 0.1, SI 5000 "
            f"...) cannot be guessed. Export the full Activity Statement "
            f"(it lists every instrument).")

    def _check_trade_money(self, where: str, symbol: str, qty: float,
                           price: float, proceeds: float, comm: float,
                           mult: float, check_notional: bool) -> None:
        """Per-row money check that FAILS CLOSED: a header-only column
        swap (Proceeds <-> Comm/Fee) or a wrong multiplier is refused
        instead of booked. A Proceeds sign that contradicts the quantity
        only warns (the magnitude is what is booked; the Cash Report
        reconciliation catches a real sign error)."""
        gross = abs(proceeds)
        if check_notional:
            expected = abs(qty) * abs(price) * mult
            tol = max(0.02, 0.001 * max(expected, gross))
            if abs(gross - expected) > tol:
                raise BrokerageParseError(
                    f"{where}: {symbol}: |Proceeds| {gross:,.2f} is not "
                    f"|Quantity| {abs(qty):g} x T. Price {price:g} x "
                    f"multiplier {mult:g} = {expected:,.2f} — a swapped "
                    f"or mislabelled column, or a wrong contract "
                    f"multiplier; refusing to book it.")
            if ((qty > 0 and proceeds > 0.005)
                    or (qty < 0 and proceeds < -0.005)) and not getattr(
                        self, '_sign_warned', False):
                self._sign_warned = True
                print(f"warning: {where}: {symbol}: Proceeds "
                      f"{proceeds:,.2f} has the opposite sign to IB's "
                      f"convention for Quantity {qty:g} (a buy's proceeds "
                      f"are negative); the magnitude is booked — check "
                      f"the export (first such row only).",
                      file=sys.stderr)
        # Commissions are small next to the trade: a percentage of the
        # gross plus a per-contract/per-share allowance and a floor.
        allowed = 0.25 * gross + abs(qty) + 10.0
        if abs(comm) > allowed:
            raise BrokerageParseError(
                f"{where}: {symbol}: Comm/Fee {comm:,.2f} is implausible "
                f"for a trade of gross {gross:,.2f} (qty {qty:g}) — a "
                f"swapped or mislabelled column; refusing to book it.")

    def _book_forex(self, row: List[str], header_map: Dict[str, int],
                    where: str, book) -> None:
        """A Trades/Forex conversion is not a tax event here, but its
        cash IS in the Cash Report's Trades and Commissions lines — book
        it for the reconciliation only."""
        self.require_columns(header_map, ('Symbol', 'Quantity',
                                          'Proceeds', 'Currency'),
                             section='Trades (Forex)', where=where)
        sym = self._cell(row, header_map, 'Symbol')
        base, _, quote = sym.partition('.')
        if not base or not quote:
            raise BrokerageParseError(
                f"{where}: Forex symbol {sym!r} is not BASE.QUOTE")
        qty = parse_strict_number(self._cell(row, header_map, 'Quantity'),
                                  field='Quantity', where=where)
        proceeds = parse_strict_number(
            self._cell(row, header_map, 'Proceeds'), field='Proceeds',
            where=where)
        book('Trades (Sales + Purchase)', quote, proceeds)
        book('Trades (Sales + Purchase)', base, qty)
        comm_col = next((c for c in header_map
                         if re.match(r'^Comm in [A-Z]{3}$', c or '')), None)
        if comm_col:
            book('Commissions', comm_col[-3:], parse_strict_number(
                self._cell(row, header_map, comm_col), field=comm_col,
                where=where, allow_blank=True, blank=0.0))
        elif 'Comm/Fee' in header_map:
            book('Commissions',
                 self._cell(row, header_map, 'Currency') or quote,
                 parse_strict_number(
                     self._cell(row, header_map, 'Comm/Fee'),
                     field='Comm/Fee', where=where, allow_blank=True,
                     blank=0.0))

    def _option_symbol(self, raw: str, asset_cat: str, fii: Dict[tuple, Any],
                       root_alias: Dict[str, str],
                       aliased_roots: Dict[str, str], where: str) -> str:
        """OCC symbol for an IB option row ("XSP 16JAN26 68.5 P",
        futures-option monthly "CL JAN26 52 P", legacy "SPX 20241220 P
        4000"). The root is canonicalized through the statement's
        option-root aliases (one conid listed as DFDV 251121P... and
        DFDV1 251121P... after a corporate action), so the opening and
        the assigned leg share ONE symbol and the premium folds."""
        symbol = raw
        opt_match = re.search(r'^(.+?)\s+(\d{2})([A-Z]{3})(\d{2})\s+([\d\.]+)\s+([PC])$', raw)
        opt_match_monthly = (re.search(r'^(.+?)\s+([A-Z]{3})(\d{2})\s+([\d\.]+)\s+([PC])$', raw)
                             if not opt_match else None)
        opt_match_legacy = (re.search(r'^(.+?)\s+(\d{8})\s+([PC])\s+([\d\.]+)', raw)
                            if not (opt_match or opt_match_monthly) else None)
        if opt_match:
            base, day, mon, yr, strike, right = opt_match.groups()
            month = _MON_MAP.get(mon.upper())
            if month:
                if asset_cat == 'Equity and Index Options':
                    base = self._alias_root(base.strip(), f"{yr}{month}{day}",
                                            right, strike, root_alias,
                                            aliased_roots, where)
                base = base.replace(' ', '.')
                symbol = f"{base}{yr}{month}{day}{right}{encode_occ_strike(strike)}"
        elif opt_match_monthly:
            base, mon, yr, strike, right = opt_match_monthly.groups()
            month = _MON_MAP.get(mon.upper())
            if month:
                base = base.replace(' ', '.')
                # A monthly futures option names only the DELIVERY
                # month; the contract's real last trading day is in
                # the Financial Instrument Information (CL JAN26 ->
                # 2025-12-16). The old placeholder day "20" invented
                # an expiry — in the wrong month, possibly the wrong
                # tax year.
                s = raw.strip()
                info = (fii.get((asset_cat, s))
                        or fii.get((asset_cat, re.sub(r'\s+', ' ', s))))
                exp = (info or {}).get('expiry') or ''
                if _IB_DATE_RE.match(exp):
                    ymd = f"{exp[2:4]}{exp[5:7]}{exp[8:10]}"
                else:
                    ymd = f"{yr}{month}20"
                    if raw not in self._placeholder_warned:
                        self._placeholder_warned.add(raw)
                        print(f"warning: {where}: {raw!r}: no expiry in "
                              f"the Financial Instrument Information — "
                              f"placeholder day 20 used in the option "
                              f"symbol (it will not match a statement "
                              f"that has the real expiry).",
                              file=sys.stderr)
                symbol = f"{base}{ymd}{right}{encode_occ_strike(strike)}"
        elif opt_match_legacy:
            base, exp, right, strike = opt_match_legacy.groups()
            base = base.replace(' ', '.')
            symbol = f"{base}{exp[2:]}{right}{encode_occ_strike(strike)}"
        # Strip all spaces as a fallback / cleanup
        return symbol.replace(' ', '')

    def _alias_root(self, base: str, yymmdd: str, right: str, strike: str,
                    root_alias: Dict[str, str],
                    aliased_roots: Dict[str, str], where: str) -> str:
        canon = root_alias.get(base)
        if not canon:
            return base
        key = (yymmdd, right, encode_occ_strike(strike))
        conids = self._ib_pre.get('contract_conids', {}).get(key, {})
        a, c = conids.get(base, set()), conids.get(canon, set())
        if a and c and not (a & c):
            # Two DIFFERENT listed contracts share the key under both
            # roots (a new standard series next to the adjusted one):
            # they are not the same security — keep them apart.
            print(f"warning: {where}: option root {base} is an alias of "
                  f"{canon} elsewhere in this statement, but "
                  f"{base} {yymmdd}{right} is a different contract than "
                  f"{canon} {yymmdd}{right} — not merged.",
                  file=sys.stderr)
            return base
        if base not in aliased_roots:
            aliased_roots[base] = canon
            print(f"note: option root {base} is IB's post-corporate-"
                  f"action alias of {canon} (same contract id in the "
                  f"Financial Instrument Information) — booked as "
                  f"{canon} so its opening and closing/assigned legs "
                  f"share one symbol.", file=sys.stderr)
        return canon

    def _reconcile_cash_report(self, pre: Dict[str, Any],
                               booked: Dict[tuple, float],
                               path: Path) -> None:
        """Every money row the parser booked must reproduce IB's own
        Cash Report, per currency, within 0.02: Dividends, Payment In
        Lieu, Withholding Tax, Broker Interest, Other Fees, Commissions
        (+ Transaction Fees, a breakdown of the same charges) and Trades
        (Sales + Purchase). A mismatch means a row was dropped, doubled
        or mis-signed — the parse FAILS rather than emit a book that
        disagrees with the broker."""
        if not pre['has_cash_report']:
            return
        if not pre['cash_currencies']:
            print(f"note: {path.name}: the Cash Report has no per-currency "
                  f"rows — parsed money not reconciled against it.",
                  file=sys.stderr)
            return
        lines = (
            ('Dividends', ('Dividends',)),
            ('Payment In Lieu of Dividends',
             ('Payment In Lieu of Dividends',)),
            ('Withholding Tax', ('Withholding Tax',)),
            ('Broker Interest Paid and Received',
             ('Broker Interest Paid and Received',)),
            ('Other Fees', ('Other Fees',)),
            ('Commissions', ('Commissions', 'Transaction Fees')),
            ('Trades (Sales + Purchase)',
             ('Trades (Sales)', 'Trades (Purchase)')),
        )
        currencies = set(pre['cash_currencies']) | {c for _, c in booked}
        bad = []
        for cur in sorted(currencies):
            for label, cr_lines in lines:
                broker = sum(pre['cash'].get((ln, cur), 0.0)
                             for ln in cr_lines)
                mine = booked.get((label, cur), 0.0)
                if abs(broker - mine) > _IB_CASH_TOL:
                    bad.append(f"{cur} {' + '.join(cr_lines)}: parsed "
                               f"{mine:,.2f} vs Cash Report {broker:,.2f} "
                               f"(diff {mine - broker:+,.2f})")
        if bad:
            raise BrokerageParseError(
                f"{path.name}: parsed rows do not reconcile with IB's own "
                f"Cash Report — {'; '.join(bad)}. A money row was "
                f"dropped, doubled or mis-signed; refusing to emit a book "
                f"that disagrees with the broker.")

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

        # Expiry rows (settle == date), for the end-of-parse clamp of
        # same-contract trades whose T+1 settle falls after the expiry
        # (BaseBrokerage.clamp_settlement_to_expiry).
        expiry_txs: List[Dict[str, Any]] = []

        # `Transaction Fees` rows (UK Stamp Tax, SEC/FINRA-style
        # levies) are a per-fill BREAKDOWN of charges IB ALREADY
        # includes in the trade's Comm/Fee: on the real 2025 statement a
        # 10,000-share AWE buy carries Comm/Fee -54.34 and Basis
        # 9,934.34 (= 9,880 + 54.34) while its two stamp-tax rows sum
        # to -49.40, and the Cash Report shows Commissions GBP -12.91 +
        # Transaction Fees -49.40 = the -62.31 sum of the GBP Comm/Fee
        # column. Folding the levy into the trade AGAIN (the earlier
        # behaviour) charged it twice (fee 103.74 instead of 54.34).
        # The section is therefore a recognized non-event: the Cash
        # Report reconciliation below checks the identity
        #   sum(Comm/Fee) == Commissions + Transaction Fees
        # per currency, so a statement layout that ever EXCLUDED the
        # levy from Comm/Fee would fail loudly instead of under-booking.

        # Cash booked per (Cash Report line, currency), for the end-of-
        # parse reconciliation against IB's own Cash Report.
        cash_booked: Dict[tuple, float] = {}

        def _book(line: str, cur: str, amt: float) -> None:
            cash_booked[(line, cur)] = cash_booked.get((line, cur),
                                                       0.0) + amt

        # Corporate Actions rows already translated, so a later `Ca`
        # (cancellation) row can find and undo its original — a
        # cancelled split/merger/cash-in-lieu used to stay booked.
        ca_effects: List[Dict[str, Any]] = []
        pending_ca: List[Dict[str, Any]] = []
        # Unknown (non-allowlisted) sections already warned about.
        unknown_sections: set = set()

        def _ca_apply_undo(eff: Dict[str, Any]) -> None:
            """Undo one translated Corporate Actions row (its `Ca`
            cancellation arrived)."""
            eff['consumed'] = True
            drop = {id(t) for t in eff.get('txs', ())}
            kind = eff['kind']
            if kind == 'split':
                info = eff['split']
                if eff['qty'] > 0:
                    info['new'] -= eff['qty']
                elif eff['qty'] < 0:
                    info['old'] += eff['qty']
                if abs(info['new']) < 1e-9 and abs(info['old']) < 1e-9:
                    # No leg left: the split never happened.
                    drop.add(id(info['tx']))
                    emitted_splits.pop(info['key'], None)
                else:
                    info['tx']['quantity'] = info['text_ratio']
                    _ib_refine_split_ratio(info)
            elif kind == 'cil':
                info = eff.get('split')
                if info is not None:
                    info['cil'] -= eff['frac']
                    info['tx']['quantity'] = info['text_ratio']
                    _ib_refine_split_ratio(info)
                else:
                    pending_cil[eff['symbol']] = (
                        pending_cil.get(eff['symbol'], 0.0) - eff['frac'])
            elif kind == 'tender':
                tl = eff['tender']
                tl['parked'] -= eff.get('parked', 0.0)
                if eff.get('cash'):
                    tl['cash_qty'] -= -eff['qty']
                    tl['cash'] -= eff['cash']
            elif kind == 'unhandled':
                t = eff['ticker']
                unhandled_ca_tickers[t] = unhandled_ca_tickers.get(t, 0) - 1
                if unhandled_ca_tickers[t] <= 0:
                    del unhandled_ca_tickers[t]
            if drop:
                transactions[:] = [t for t in transactions
                                   if id(t) not in drop]

        def _ca_matches(ca: Dict[str, Any], eff: Dict[str, Any]) -> bool:
            return (not eff['consumed'] and eff['desc'] == ca['desc']
                    and abs(eff['qty'] + ca['qty']) < 1e-9)

        def _ca_undo(ca: Dict[str, Any]) -> bool:
            """A `Ca` row: undo the latest matching original (same
            description, negated quantity; same date preferred)."""
            cands = [e for e in ca_effects if _ca_matches(ca, e)]
            if not cands:
                return False
            same = [e for e in cands if e['date'] == ca['date']]
            _ca_apply_undo((same or cands)[-1])
            return True

        def _ca_record(eff: Dict[str, Any]) -> None:
            """Remember a translated row; a `Ca` row that arrived
            BEFORE it (same description, negated quantity) undoes it
            now."""
            ca_effects.append(eff)
            for i, ca in enumerate(pending_ca):
                if _ca_matches(ca, eff):
                    pending_ca.pop(i)
                    _ca_apply_undo(eff)
                    self.note_row_consumed()      # the waiting Ca row
                    return

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
        self._placeholder_warned: set = set()
        self._sign_warned = False

        rows = self._read_rows(path)
        pre = _ib_prescan(rows, path.name)
        self._ib_pre = pre
        self._check_statement_kind(pre, path)
        fii = pre['fii']
        root_alias = pre['root_alias']
        aliased_roots: Dict[str, str] = {}
        if len(pre['accounts']) > 1:
            print(f"warning: {path.name}: IB statement spans "
                  f"{len(pre['accounts'])} accounts ("
                  f"{', '.join(sorted(_mask_account(a) for a in pre['accounts']))}"
                  f") — every row is booked to ONE account label. That "
                  f"is right only when they are one tax entity (e.g. two "
                  f"taxable margin accounts); export a registered "
                  f"account (TFSA/RRSP) separately.", file=sys.stderr)
        header_maps = {} # section -> header_map
        # Whether this file's Trades section carries Order-level
        # rows (DataDiscriminator): once seen, execution-level
        # 'Trade' rows are duplicates and are skipped.
        _seen_order_level = False

        for lineno, row in enumerate(rows, 1):
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
                where = f"{path.name} line {lineno} ({section})"
                if section == 'Trades':
                    self.require_columns(header_map, ('Asset Category',),
                                         section=section, where=where)
                    asset_cat = self._cell(row, header_map,
                                           'Asset Category')
                else:
                    asset_cat = 'Equity and Index Options'

                # Roll-up rows carry the asset category of what
                # they sum (`Total,Forex,...`), so they must be
                # recognized BEFORE the per-category dispatch or a
                # Forex subtotal counts as one more conversion.
                _pre_disc = self._cell(row, header_map, 'DataDiscriminator')
                if _pre_disc in ('SubTotal', 'Total'):
                    self.count_skip(f"{_NE}Trades roll-up row "
                                    f"({_pre_disc})")
                    continue

                if asset_cat not in ('Stocks', 'Equity and Index Options', 'Futures', 'Options On Futures', 'Warrants'):
                    if asset_cat == 'Forex':
                        # One detail level only (as for securities):
                        # Order rows when the file has them.
                        if (_pre_disc == 'Order'
                                or (_pre_disc in ('', 'Trade')
                                    and not pre['has_order_level'])):
                            forex_rows += 1
                            self._book_forex(row, header_map, where,
                                             _book)
                        self.count_skip(
                            f"{_NE}Trades/Forex (currency conversion, "
                            f"not modeled — KNOWN_ISSUES)")
                    elif asset_cat in ('Total', '') or 'Total' in asset_cat:
                        self.count_skip(f"{_NE}Trades subtotal row")
                    else:
                        # Bonds, CFDs, ... — a real asset class the
                        # parser has no branch for: loud bucket (and
                        # the Cash Report reconciliation below fails
                        # on the cash it moved).
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
                    _disc = _pre_disc
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

                # Every money/quantity/price/date/currency column is
                # REQUIRED and resolved by header name — a renamed or
                # missing column used to read as 0 (Comm/Fee renamed:
                # every fee vanished; Proceeds renamed: every gross 0)
                # while the parse "succeeded".
                proceeds_key = ('Proceeds' if 'Proceeds' in header_map
                                else 'Notional Value')
                if section == 'Trades':
                    self.require_columns(
                        header_map,
                        ('Currency', 'Symbol', 'Date/Time', 'Quantity',
                         'T. Price', ('Proceeds', 'Notional Value'),
                         'Comm/Fee', 'Code'),
                        section=section, where=where)
                else:
                    # An expiry carries no money; its money columns
                    # are optional here, but parsed strictly when
                    # present.
                    self.require_columns(
                        header_map,
                        ('Currency', 'Symbol', 'Date/Time', 'Quantity'),
                        section=section, where=where)

                def _num(col, _where=where, _row=row, _hm=header_map,
                         optional=False):
                    if col not in _hm:
                        if optional:
                            return 0.0
                        raise BrokerageParseError(
                            f"{_where}: missing column {col!r}")
                    return parse_strict_number(
                        self._cell(_row, _hm, col), field=col,
                        where=_where)

                symbol = self._cell(row, header_map, 'Symbol')
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
                currency = self._cell(row, header_map, 'Currency')
                if not symbol or not currency:
                    raise BrokerageParseError(
                        f"{where}: blank Symbol/Currency on a "
                        f"{asset_cat} trade row")
                date, time = _ib_split_datetime(
                    self._cell(row, header_map, 'Date/Time'), where)
                date_settle = get_ib_settlement(date, asset_cat, currency)
                qty = _num('Quantity')
                opt_exp = section == 'Options Expirations'
                price = _num('T. Price', optional=opt_exp)
                proceeds_signed = _num(proceeds_key, optional=opt_exp)
                comm_signed = _num('Comm/Fee', optional=opt_exp)
                gross_proceeds = abs(proceeds_signed)
                mult = self._multiplier(asset_cat, symbol, fii, where)
                self._check_trade_money(
                    where, symbol, qty, price, proceeds_signed,
                    comm_signed, mult,
                    check_notional=(proceeds_key in header_map))

                # SIGNED commission. IB reports a charge NEGATIVE and a
                # rebate (option exchange/ORF rebates, a cancelled
                # trade's refunded commission) POSITIVE. The engine's
                # `fee` is positive = charged, so fee = -Comm/Fee, and
                # a rebate is a NEGATIVE fee. abs() turned every rebate
                # into a charge — a net error of 2x the rebate on each
                # of 65 rows (245.95 USD) in the real 2025 margin
                # statement and 195 rows (624.94 USD) in 2026.
                # Cash-true net: a buy costs -(Proceeds + Comm/Fee), a
                # sell brings in Proceeds + Comm/Fee.
                comm_fee = -comm_signed
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
                code = self._cell(row, header_map, 'Code')
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

                # Expiry: the `Ep` code, a row of the Options
                # Expirations section, or a zero-price zero-proceeds
                # CLOSE of an option that isn't an assignment/
                # exercise. An expiry has no settlement cycle — the
                # contract ceases to exist on its expiry date — so
                # date_settle == date. The T+1 applied to every
                # Trades row pushed a Dec-31 expiry into the NEXT
                # tax year on the (Canadian) settle-date basis.
                # Assignment/exercise option legs keep T+1: their
                # premium rolls into the stock leg, which really
                # settles T+1, and the pair must share a settle
                # date so no unrelated same-underlying trade can
                # consume the staged premium in between.
                is_expiry = (
                    section == 'Options Expirations'
                    or 'Ep' in code_tokens
                    or (asset_cat in ('Equity and Index Options',
                                      'Options On Futures')
                        and action != 'ASSIGN'
                        and 'C' in code_tokens
                        and abs(price) < 1e-9
                        and gross_proceeds < 1e-9))
                if is_expiry and date:
                    date_settle = date

                if asset_cat in ('Equity and Index Options', 'Options On Futures'):
                    symbol = self._option_symbol(
                        symbol, asset_cat, fii, root_alias,
                        aliased_roots, where)
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

                _trade_tx = {
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
                    # Contract size (IB's Financial Instrument
                    # Information Multiplier; 100 per equity option,
                    # 1000 per CL future, 0.1 per MET/MBT, 1 per
                    # share). The schema notional check uses it —
                    # futures used to trip it on every row.
                    'multiplier': mult,
                    'account': 'IB',
                    'description': description,
                }
                transactions.append(_trade_tx)
                if is_expiry:
                    expiry_txs.append(_trade_tx)
                # Cash Report: futures settle daily through "Cash
                # Settling MTM", never through Trades (Sales/Purchase).
                if asset_cat != 'Futures':
                    _book('Trades (Sales + Purchase)', currency,
                          proceeds_signed)
                _book('Commissions', currency, comm_signed)
                self.note_row_consumed()

            elif section == 'Dividends':
                # Required columns resolved by header name; a missing
                # one FAILS the parse (it used to fall back to row[0] —
                # the literal section name — and later to a loud skip
                # that still lost the income).
                where = f"{path.name} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                # IB emits per-currency subtotal rows ('Total' in the
                # currency or description cell) — skip before parsing.
                if 'Total' in currency or 'Total' in description:
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)
                # SIGN-PRESERVING: IB posts re-characterizations as a
                # negative reversal row plus a corrected positive row.
                # abs() here used to book all three legs as income
                # (+250 / −250 / +240 → 740 instead of 240); keeping
                # the sign lets the reversal net out downstream.
                amount = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)

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
                is_pil = ('payment in lieu of dividend' in desc_lower
                          or 'in lieu of dividend' in desc_lower)
                _book('Payment In Lieu of Dividends' if is_pil
                      else 'Dividends', currency, amount)
                if is_roc_description(description):
                    # IB's "(Return of Capital)" label is the ISSUER's
                    # designation, which is only an ACB reduction for
                    # Canadian purposes in the Canadian-issuer case.
                    #  * A PAYMENT IN LIEU is paid by the share
                    #    borrower, not the issuer: it can never reduce
                    #    ACB, whatever the underlying distribution was
                    #    — income (falls through to the PIL branch).
                    #  * A non-resident corporation's pro-rata
                    #    distribution is deemed a DIVIDEND by ITA
                    #    s.90(2); a US "return of capital" (no E&P)
                    #    doesn't make it a PUC reduction (the s.90(3)
                    #    qualifying-return-of-capital exception is for
                    #    foreign affiliates only). Default: foreign
                    #    dividend; [settings] foreign_return_of_capital
                    #    = "acb" restores the ACB treatment.
                    #  * Canadian issuer (T3 box 42 style): ACB
                    #    reduction, as before.
                    # No ISIN → issuer unknown → kept as ADJUST.
                    _issuer_cc = isin[:2].upper() if len(isin) >= 2 else ''
                    _foreign = bool(_issuer_cc) and _issuer_cc != 'CA'
                    if is_pil:
                        description = (
                            f"{description} [payment in lieu of an "
                            f"IB-designated return of capital: paid by "
                            f"the share borrower — income, never an ACB "
                            f"reduction]")
                    elif (_foreign and self.foreign_return_of_capital
                          != 'acb'):
                        description = (
                            f"{description} [IB-designated return of "
                            f"capital, treated as a dividend under ITA "
                            f"s.90(2)]")
                    else:
                        # `amount` stays signed so IB's negative
                        # reversal rows net out (they become positive
                        # ADJUSTs).
                        transactions.append(self.tx_roc_adjust(
                            symbol=f"{ticker}.{ext}", currency=currency,
                            date=date, desc=description, amount=amount,
                            account='IB'))
                        self.note_row_consumed()
                        continue
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
                # IB packs several codes into the cell ('ADR;Po' on an
                # ADR's accrual): match the Po/Re TOKEN, not the cell.
                _acc_tokens = set(re.split(r'[;,\s]+', self._cell(
                    row, header_map, 'Code')))
                code = ('Po' if 'Po' in _acc_tokens
                        else 'Re' if 'Re' in _acc_tokens else '')
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
                # Keyed on the EX date, not the pay date: IB revises
                # a dividend's pay date between the Po and the Re row
                # (a real ENB accrual posted pay 03-01, reversed pay
                # 03-02), and a pay-date key split the pair into two
                # half-open accruals — a false "accrued but not
                # booked" warning for a dividend already paid. The
                # pay date is only a fallback key when IB omits the
                # ex date. Latest row's pay date wins for display.
                key = ((account, symbol, 'ex', ex_date) if ex_date
                       else (account, symbol, 'pay', pay_date))
                accrual_net[key] = accrual_net.get(key, 0.0) + gross
                self.note_row_consumed()      # read into parser state
                _meta = accrual_meta.setdefault(key, {
                    'symbol': symbol, 'pay_date': pay_date,
                    'currency': currency, 'pay_dates': set(),
                })
                if pay_date:
                    _meta['pay_date'] = pay_date
                    _meta['pay_dates'].add(pay_date)
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
                # Required columns — see the Dividends section.
                where = f"{path.name} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                if 'Total' in currency:
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)
                # IB books withholding as a NEGATIVE amount (cash out)
                # and a refund/correction as POSITIVE. The repo-wide
                # TAX convention is positive = tax withheld (RBC emits
                # it that way), so flip the sign rather than abs() it:
                # a charge stays positive and a refund nets NEGATIVE
                # instead of double-counting as more tax paid.
                _wht_cash = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)
                amount = -_wht_cash
                _book('Withholding Tax', currency, _wht_cash)

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
                # Required columns — see the Dividends section.
                where = f"{path.name} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                if 'Total' in currency:
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)
                amount = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)
                _book('Broker Interest Paid and Received', currency, amount)

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
                # Required columns — see the Dividends section. (Date
                # may be BLANK on a row: the "for Mmm YYYY" fallback
                # below; the COLUMN must exist.)
                where = f"{path.name} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                date = self._cell(row, header_map, 'Date')
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

                # IB books a charge NEGATIVE (cash out) and a
                # refund/reversal POSITIVE. Repo FEE convention
                # (taxjson_fx_cash, .tt round-trip): positive =
                # charged — flip, like the Withholding Tax
                # branch does for TAX, so a market-data charge
                # is an outflow and a reversal nets against it.
                _fee_cash = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)
                amount = -_fee_cash
                if date:
                    date = _ib_require_date(date, where)
                else:
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

                _book('Other Fees', currency, _fee_cash)
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
                # A per-fill BREAKDOWN of levies already inside the
                # trade's Comm/Fee (see the note at the top of
                # parse_file) — a recognized non-event.
                currency = self._cell(row, header_map, 'Currency')
                _tf_cat = self._cell(row, header_map, 'Asset Category')
                if (not currency or 'Total' in currency
                        or _tf_cat.startswith('Total')):
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                self.count_nonevent(
                    f"{section} row (breakdown of a levy already in the "
                    f"trade's Comm/Fee)")

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
                where = f"{path.name} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                if not currency or 'Total' in currency:
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)
                amount = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)
                _book('Commissions', currency, amount)
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
                # Required columns resolved by header name; a missing
                # one fails the parse (Quantity/Value/Proceeds are the
                # share and cash legs, Code carries the `Ca` marker a
                # restatement depends on).
                where = f"{path.name} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date/Time', 'Description',
                                 'Quantity', 'Proceeds', 'Value', 'Code'),
                    section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                # Subtotal rows (Asset Category 'Total', blank
                # Currency) must be recognized BEFORE the currency
                # → suffix lookup: the blank currency used to reach
                # _ib_currency_ext and fire the "IB currency '' has
                # no exchange-suffix mapping" warning on every
                # statement with a Corporate Actions total.
                _ca_cat = self._cell(row, header_map, 'Asset Category')
                if (not currency or 'Total' in currency
                        or _ca_cat.startswith('Total')):
                    self.count_nonevent(f"{section} subtotal row")
                    continue

                date, time = _ib_split_datetime(
                    self._cell(row, header_map, 'Date/Time'), where)
                qty = parse_strict_number(
                    self._cell(row, header_map, 'Quantity'),
                    field='Quantity', where=where)
                val = parse_strict_number(
                    self._cell(row, header_map, 'Value'), field='Value',
                    where=where)
                # Cash actually paid (cash in lieu, a cash tender).
                proceeds = parse_strict_number(
                    self._cell(row, header_map, 'Proceeds'),
                    field='Proceeds', where=where)

                ext = _ib_currency_ext(currency)

                # Cancel/rebook restatements: IB re-lists a corrected
                # corporate action as original + `Ca` cancellation
                # (same description, negated quantity) + rebooked
                # rows. Every translated row records its effect in
                # `ca_effects`; a Ca row UNDOES its original's effect
                # — a cancelled split's legs come off the split (the
                # SPLIT row goes when no leg is left), a cancelled
                # cash-in-lieu / tender sale / spinoff removes the
                # rows it emitted, a cancelled untranslated merger
                # leaves the unhandled tally. Only the split branch
                # used to be spared the doubling; a restated split
                # 3:1 -> 2:1 emitted BOTH ratios. A Ca row whose
                # original is not (yet) seen waits for it; unmatched
                # at end of file it is a LOUD skip.
                _cacode = self._cell(row, header_map, 'Code')
                if 'Ca' in re.split(r'[;,\s]+', _cacode or ''):
                    _ca = {'desc': description, 'date': date,
                           'qty': qty, 'where': where}
                    if _ca_undo(_ca):
                        self.note_row_consumed()  # undid its original
                    else:
                        pending_ca.append(_ca)
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
                _eff = {'desc': description, 'date': date, 'qty': qty,
                        'kind': 'none', 'txs': [], 'consumed': False}
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
                    _eff.update(kind='tender', tender=_tl,
                                parked=qty if _is_placeholder else 0.0)
                    if abs(proceeds) < 0.005:
                        if _is_placeholder:
                            _tl['parked'] += qty
                        self.count_nonevent(
                            f"{section} tender/voluntary-offer share "
                            f"journal (zero proceeds)")
                        _ca_record(_eff)
                        continue
                    if qty < 0:
                        _cash = abs(proceeds)
                        _ttx = {
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
                            'multiplier': 1.0,
                            'account': 'IB',
                            'description': description,
                        }
                        transactions.append(_ttx)
                        _tl['cash_qty'] += -qty
                        _tl['cash'] += _cash
                        if _is_placeholder:
                            _tl['parked'] += qty
                        _eff.update(txs=[_ttx], cash=_cash)
                        self.note_row_consumed()
                        _ca_record(_eff)
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
                    _ctx = {
                        'action': 'BUYSELL',
                        'date': date,
                        'time': time,
                        'date_settle': date,
                        'symbol': symbol,
                        'quantity': qty,
                        'currency': currency,
                        'price': round(cash / -qty, 8),
                        'net_amount': cash,
                        'multiplier': 1.0,
                        'account': 'IB',
                        'description': description
                    }
                    transactions.append(_ctx)
                    _eff.update(kind='cil', txs=[_ctx], symbol=symbol,
                                frac=-qty, split=None)
                    # The fraction belongs to this symbol's latest
                    # split on or before this date; a CIL row filed
                    # ahead of its legs waits for them.
                    _cands = [i for (s, d, _r), i in emitted_splits.items()
                              if s == symbol and d <= date]
                    if _cands:
                        _info = max(_cands, key=lambda i: i['tx']['date'])
                        _info['cil'] += -qty
                        _ib_refine_split_ratio(_info)
                        _eff['split'] = _info
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
                                'text_ratio': ratio, 'key': split_key,
                                'cil': pending_cil.pop(symbol, 0.0)}
                        emitted_splits[split_key] = info
                    if qty > 0:
                        info['new'] += qty
                    elif qty < 0:
                        info['old'] += -qty
                    _ib_refine_split_ratio(info)
                    _eff.update(kind='split', split=info)
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
                    _sd = {
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
                    }
                    _sb = {
                        'action': 'BUYSELL',
                        'date': date,
                        'time': time,
                        'date_settle': date,
                        'symbol': symbol,
                        'quantity': qty,
                        'currency': currency,
                        'price': price,
                        'net_amount': abs(val),
                        'multiplier': 1.0,
                        'account': 'IB',
                        'description': description
                    }
                    transactions.extend((_sd, _sb))
                    _eff.update(kind='spinoff', txs=[_sd, _sb])
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
                    _eff.update(kind='unhandled', ticker=ticker)
                else:
                    self.count_nonevent(f"{section} zero-quantity row")
                _ca_record(_eff)

            elif section == 'Transfers':
                # Required columns resolved by header name; a missing
                # one fails the parse.
                where = f"{path.name} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Asset Category', 'Type', 'Symbol',
                                 'Currency', 'Date', 'Qty',
                                 'Market Value', 'Code'),
                    section=section, where=where)
                asset_cat = self._cell(row, header_map, 'Asset Category')
                transfer_type = self._cell(row, header_map, 'Type')
                symbol = self._cell(row, header_map, 'Symbol')
                currency = self._cell(row, header_map, 'Currency')
                qty_str = self._cell(row, header_map, 'Qty')
                if not asset_cat or asset_cat.startswith('Total'):
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                if asset_cat not in ('Stocks', 'Equity and Index Options',
                                     'Warrants', 'Futures',
                                     'Options On Futures'):
                    # Cash movements (no security) are custody of
                    # money, not property; any other asset class is a
                    # security the parser has no branch for — loud.
                    if asset_cat in ('Cash', 'Forex') or not symbol:
                        self.count_nonevent(f"{section} cash row")
                    else:
                        self.count_skip(f"{section}/{asset_cat}")
                    continue
                if not qty_str:
                    self.count_nonevent(f"{section} row with no "
                                        f"quantity")
                    continue
                qty = parse_strict_number(qty_str, field='Qty',
                                          where=where)
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)

                # Skip cash transfers
                if symbol.startswith('CASH.'):
                    self.count_nonevent(f"{section} cash transfer")
                    continue

                total_cost = parse_strict_number(
                    self._cell(row, header_map, 'Market Value'),
                    field='Market Value', where=where)

                multiplier = self._multiplier(asset_cat, symbol, fii,
                                              where)
                if asset_cat in ('Equity and Index Options',
                                 'Options On Futures'):
                    symbol = self._option_symbol(
                        symbol, asset_cat, fii, root_alias,
                        aliased_roots, where)
                    if asset_cat == 'Options On Futures':
                        symbol = f"F:{symbol}"
                elif asset_cat == 'Futures':
                    symbol = f"F:{symbol.replace(' ', '.')}"
                else:
                    symbol = symbol.replace(' ', '.')

                symbol = re.sub(r'\.(TO|US|AX|L)$', '', symbol, flags=re.IGNORECASE)
                ext = _ib_currency_ext(currency)
                symbol = f"{symbol}.{ext}"

                abs_qty = abs(qty)
                price = (round(abs(total_cost) / (abs_qty * multiplier), 8)
                         if abs_qty > 1e-6 and multiplier else 0.0)

                # Cancel/rebook: IB lists a reversed ACATS/ATON leg
                # as original + `Ca` cancellation (opposite qty,
                # same date) + rebooked rows. A real RRSP move
                # (IB -> Questrade) carried one symbol five times: Out,
                # Ca, Out, Ca, Out — arithmetically -N, but the
                # two Ca legs read as +N ACQUISITIONS to the
                # superficial-loss walk. The Ca row consumes its
                # original; only when the original sits in an
                # earlier statement does the reversal stay as a
                # netting leg.
                _xcode = self._cell(row, header_map, 'Code')
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

            elif section in _IB_METADATA_SECTIONS:
                # Statement metadata / roll-ups (Account Information,
                # Net Asset Value, Cash Report, Mark-to-Market,
                # Financial Instrument Information, ...) — not events:
                # counted under the calmer note, never dropped
                # unaccounted.
                self.count_nonevent(f"section {section} (not "
                                    f"translated)")
            else:
                # A section with no branch and not on the metadata
                # allowlist: a renamed/localized money section
                # ("Dividendes", "Transactions") or one IB added.
                # With money-like columns it is an ERROR (its cash
                # would silently vanish); otherwise a loud skip.
                _money = sorted(_IB_MONEY_COLUMNS & set(header_map))
                if _money:
                    raise BrokerageParseError(
                        f"{path.name} line {lineno}: unknown IB section "
                        f"{section!r} carries money columns "
                        f"({', '.join(_money)}) and has no parser branch "
                        f"— refusing to drop its rows. Re-export the "
                        f"statement in English, or report the section.")
                if section not in unknown_sections:
                    unknown_sections.add(section)
                    print(f"warning: {path.name}: unknown IB section "
                          f"{section!r} (no parser branch, not known "
                          f"metadata) — its rows are skipped; check "
                          f"they carry no trades or income.",
                          file=sys.stderr)
                self.count_skip(f"section {section} (unknown)")

        # `Ca` rows whose original never appeared: a cancellation of
        # something booked in an EARLIER statement (or a restatement
        # this parser cannot pair). Nothing is guessed — loud skip.
        for _ca in pending_ca:
            print(f"warning: {_ca['where']}: IB cancelled (Ca) the "
                  f"corporate action {_ca['desc']!r} ({_ca['qty']:g} on "
                  f"{_ca['date']}) but its original row is not in this "
                  f"statement — nothing undone; if the original was "
                  f"booked from an earlier statement, reverse it by "
                  f"hand in a .tt file.", file=sys.stderr)
            self.count_skip("Corporate Actions Ca row whose original is "
                            "not in this statement (see warning)")

        # Transaction Fees are inside Comm/Fee while the Cash Report
        # books them on their own line, so the commission identity is
        #   sum(Comm/Fee) + Commission Adjustments
        #     == Commissions + Transaction Fees.
        self._reconcile_cash_report(pre, cash_booked, path)

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
        def _posted_near(sym: str, pay: str) -> bool:
            if (sym, pay) in posted_dividend_keys:
                return True
            try:
                _pd = datetime.strptime(pay, "%Y-%m-%d")
            except (TypeError, ValueError):
                return False
            for _sym, _d in posted_dividend_keys:
                if _sym != sym:
                    continue
                try:
                    if abs((datetime.strptime(_d, "%Y-%m-%d")
                            - _pd).days) <= 7:
                        return True
                except ValueError:
                    continue
            return False

        open_accruals = []
        for key, net in accrual_net.items():
            if net <= 0.01:
                continue
            meta = accrual_meta[key]
            # Posted within a week of ANY pay date the accrual carried
            # (revised pay dates; the cash may post in another currency
            # — an accrual in USD for a dividend IB pays in CAD — so the
            # match is on ticker + date only).
            if any(_posted_near(meta['symbol'], pd)
                   for pd in (meta['pay_dates'] or {meta['pay_date']})):
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

        self.clamp_settlement_to_expiry(transactions, expiry_txs)

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
