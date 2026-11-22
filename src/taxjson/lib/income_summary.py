"""The income summary of a book's income rows (summarize_income: each row
dated by its country's rule) and the share of a dividend-compensation row
the US engine put into a cover's cost (capitalized_pil_shares).
bin/taxjson_sum_income.py prints the summary; the report model reads them
from here.
"""
from typing import List, Dict, Any

from taxjson.lib.json_input import check_row_types
from taxjson.model.result import GainRow
from taxjson.lib.ticker_map import get_underlying, is_option_ticker


_AMOUNT_KEYS = ('gross_amount', 'net_amount', 'amount')


def get_base_ticker(symbol: str) -> str:
    """Group an income symbol under its underlying, exactly like the gains
    half of the .sum does. The old local implementation split on the last
    dot and rejoined — an identity function apart from a leading-dot strip
    — so a PIL/dividend row on an OCC option symbol bucketed under the raw
    contract while sum_gains bucketed the same event's gains under the
    underlying, and the two halves of one .sum disagreed."""
    symbol = (symbol or '').lstrip('.')
    if is_option_ticker(symbol):
        underlying = get_underlying(symbol)
        if underlying:
            return underlying
    return symbol


def _is_dil(e: Dict[str, Any]) -> bool:
    return e.get('action') == 'DIVIDEND_IN_LIEU'


def _has_share(e: Dict[str, Any]) -> bool:
    """A payment in lieu with a capitalized part: the rows the share
    reads (a Canadian run writes none: TJ-2999)."""
    return _is_dil(e) and e.get('capitalized_263h') is not None


def capitalized_pil_shares(gains_rows: List[Dict[str, Any]], path="the gains rows"
                           ) -> Dict[str, float]:
    """{row id: the share of a dividend-compensation row the US engine put
    into a cover's cost (§263(h); US-INC-04)}, from a gains file's rows:
    the book's row keeps its whole amount (backlog TJ-1139, as the views'
    TJ-1066). The payments in lieu with a capitalized part go through the
    row checks first, the two fields it reads, an InputFileError naming
    `path`, the row and the field (a view would read text as a refusal
    naming no file, an integer too large for a float as inf); nothing else
    is checked (TJ-2941, TJ-2991, TJ-2992, TJ-2998, TJ-2999). The one
    copy: the views (cli/common `_capitalized_pil_shares`) and
    lib/account_report call it (TJ-2993)."""
    check_row_types(gains_rows, path, only=_has_share, fields=("capitalized_263h", "pil"))
    out: Dict[str, float] = {}
    for e in gains_rows:
        if not isinstance(e, dict) or not _has_share(e):
            continue
        v = GainRow(e)
        cap = float(v.capitalized_263h or 0.0)
        if not cap:                 # pil read only with a share (TJ-2999)
            continue
        whole = cap - float(v.pil or 0.0)
        if whole > 0:
            out[str(e.get('id'))] = min(1.0, cap / whole)
    return out


def summarize_income(transactions: List[Dict[str, Any]], target_year: int = None,
                     rules=None, capitalized: Dict[str, float] = None
                     ) -> Dict[str, Any]:
    """Process income data and create summary. `rules` (a
    characterize/income_dating.IncomeRules) dates each income row by its
    country's rule and (Canada) counts a s.260 payment in lieu as a
    dividend; without it every row is dated by its pay date and every
    payment in lieu is kept in its own column. `capitalized`
    (capitalized_pil_shares): the part of a payment in lieu that is in a
    cover's cost is no income; `capitalized_263h` gives it by currency."""
    ticker_stats: Dict[str, Dict] = {}
    interest_totals: Dict[str, float] = {}
    # Interest charged (margin interest; IB and Questrade rows keep
    # their sign): a carrying charge, never netted against interest
    # earned (KNOWN_ISSUES "Margin interest paid is netted", fixed
    # 2026-10). {currency: amount paid, positive}.
    interest_paid_totals: Dict[str, float] = {}
    # Income rows with no amount at all (every amount key missing or
    # null) — booked as $0 without a word before (audit S051-14). The
    # CLI refuses them; library callers see the count.
    missing_amount: List[Dict[str, Any]] = []
    capitalized_totals: Dict[str, float] = {}

    # The tax withheld on a payment follows its dividend's date (A2-0396).
    wh_dates = (rules.withholding_dates(transactions)
                if rules is not None else {})
    # A Canadian trust's units (the books call their payouts
    # distributions): a payment in lieu on one stays ordinary (A2-1465).
    trusts = (rules.trust_units(transactions)
              if rules is not None else frozenset())
    for tx in transactions:
        # `tx['date']` is sometimes explicitly None (came in as JSON
        # null) — `tx.get('date', '')` returns None in that case and
        # the [:4] slice would have crashed. Use `or ''` to fold both
        # missing-and-None into the empty-string path which the year
        # filter below already tolerates.
        date = str((wh_dates.get(id(tx))
                    or (rules.income_date(tx) if rules is not None
                        else tx.get('date')) or '')[:4])
        
        # Filter by year if specified
        if target_year and not date.startswith(str(target_year)):
            continue
        
        action = tx.get('action', '').upper()
        type_ = tx.get('type', '').lower()
        symbol = tx.get('symbol', 'UNKNOWN')
        # '?' for missing currency, matching sum_gains — a USD
        # default let the two halves of one .sum bucket the same
        # account's rows differently.
        currency = tx.get('currency') or '?'
        
        # Get amount - gross_amount is primary for dividends, net_amount for others
        if action in ('DIVIDEND', 'DIVIDEND_IN_LIEU') or type_ in ('dividend', 'dividend_in_lieu'):
            amount_raw = tx.get('gross_amount') or tx.get('net_amount') or tx.get('amount')
        else:
            amount_raw = tx.get('net_amount') or tx.get('gross_amount') or tx.get('amount')

        amount = float(amount_raw or 0)
        if (action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST')
                and all(tx.get(k) is None for k in _AMOUNT_KEYS)):
            missing_amount.append({k: tx.get(k) for k in
                                   ('date', 'symbol', 'action', 'account')})

        base_ticker = get_base_ticker(symbol)

        # DIVIDEND_IN_LIEU (Payment-in-Lieu): the broker had your shares
        # loaned out, so the issuer's dividend was paid to someone else and
        # you got a substitute payment. US: ordinary (non-qualified)
        # income. Canada: ordinary income, EXCEPT a Canadian dealer's
        # payment on a Canadian corporation's share, which s.260 deems a
        # taxable dividend (moved to the dividend column above). The
        # rest stays per-ticker in its own column.
        if rules is not None and rules.pil_is_dividend(tx, trusts):
            # Canada, ITA s.260: a Canadian dealer's payment in lieu on
            # a Canadian issuer's share is a taxable dividend (T5 box
            # 24), so it is in the dividend column.
            action, type_ = 'DIVIDEND', 'dividend'
        if action == 'DIVIDEND_IN_LIEU' or type_ == 'dividend_in_lieu':
            if base_ticker not in ticker_stats:
                ticker_stats[base_ticker] = {}
            if currency not in ticker_stats[base_ticker]:
                ticker_stats[base_ticker][currency] = {'div': 0.0, 'tax': 0.0, 'pil': 0.0}
            ticker_stats[base_ticker][currency].setdefault('pil', 0.0)
            share = (capitalized or {}).get(str(tx.get('id')))
            if share:
                capitalized_totals[currency] = (
                    capitalized_totals.get(currency, 0.0) + amount * share)
                amount -= amount * share
            ticker_stats[base_ticker][currency]['pil'] += amount
            continue

        if action == 'DIVIDEND' or type_ == 'dividend':
            if base_ticker not in ticker_stats:
                ticker_stats[base_ticker] = {}
            if currency not in ticker_stats[base_ticker]:
                ticker_stats[base_ticker][currency] = {'div': 0.0, 'tax': 0.0, 'pil': 0.0}
            ticker_stats[base_ticker][currency]['div'] += amount

        elif action == 'TAX' or type_ == 'tax':
            if base_ticker not in ticker_stats:
                ticker_stats[base_ticker] = {}
            if currency not in ticker_stats[base_ticker]:
                ticker_stats[base_ticker][currency] = {'div': 0.0, 'tax': 0.0, 'pil': 0.0}
            # Sign-respecting: parsers emit positive = tax withheld (RBC is
            # always positive; IB flips its negative cash amounts at parse).
            # A withholding refund/reversal arrives NEGATIVE and must net
            # against the charge — abs() double-counted refunds as tax paid.
            ticker_stats[base_ticker][currency]['tax'] += amount

        elif action == 'INTEREST' or type_ == 'interest':
            if amount < 0:
                interest_paid_totals[currency] = (
                    interest_paid_totals.get(currency, 0.0) - amount)
                continue
            if currency not in interest_totals:
                interest_totals[currency] = 0.0
            interest_totals[currency] += amount

        # taxjson_income.py puts some income under "other"
        elif type_ == 'other':
            if base_ticker not in ticker_stats:
                ticker_stats[base_ticker] = {}
            if currency not in ticker_stats[base_ticker]:
                ticker_stats[base_ticker][currency] = {'div': 0.0, 'tax': 0.0, 'pil': 0.0}
            ticker_stats[base_ticker][currency]['div'] += amount
    
    return {
        'ticker_stats': ticker_stats,
        'interest_totals': interest_totals,
        'interest_paid_totals': interest_paid_totals,
        'total_year': target_year or 'all',
        'missing_amount': missing_amount,
        **({'capitalized_263h': capitalized_totals}
           if capitalized_totals else {}),
    }
