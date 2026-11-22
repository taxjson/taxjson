"""The aggregates of a gains document (summarize_gains: a model/result
Result or its JSON). bin/taxjson_sum_gains.py prints them; the report model
reads them from here.
"""
from typing import Any, Dict, Mapping, Union

from taxjson.lib.ticker_map import get_underlying as get_base_ticker, is_option_ticker
from taxjson.model.result import Result


def summarize_gains(data: Union[Result, Mapping[str, Any]]) -> Dict[str, Any]:
    """The aggregates of a gains document: a model/result Result or its JSON."""
    result = data if isinstance(data, Result) else Result.from_json(data)
    ticker_stats: Dict[str, Dict] = {}
    returns_by_asset: Dict[str, Dict] = {} # asset_type -> currency -> {dollars: [], days: [], fees: [], fee_shares: []}
    total_fees: Dict[str, float] = {}
    option_fees: Dict[str, float] = {}
    
    # We rebuild the summary from the transaction list to ensure year-filtering and currency are correct.
    transactions = result.transactions
    target_year = result.summary.get('year') or 'all'
    # An option whose root names no share listing but one class share
    # (SAMPLD...TO with SAMPLD.B.TO shares) groups under that share (S040-11).
    from taxjson.lib.ticker_map import class_share_aliases
    # Held shares (inventory) too (re-audit A2-0715).
    _aliases = class_share_aliases(
        t.symbol for t in transactions + result.inventory)
    # Grant-timing writes not yet paired with a buy-back:
    # (account, symbol, currency) -> [[bucket, dollars index, open
    # units, tick_stats, hold_days index], ...] (A2-1114). Per account:
    # a buy-back closes its own account's write, never another's
    # (2026-10-24 audit B35).
    open_writes: Dict[Any, list] = {}
    from taxjson.lib.futures import is_plain_future

    for tx in transactions:
        symbol = tx.symbol
        if not symbol: continue

        is_opt = is_option_ticker(symbol)
        # One classifier for the returns and the fees: a futures row's
        # gain and fee land in the same Futures block (audit B34).
        asset_type = ('Options' if is_opt else
                      'Futures' if is_plain_future(symbol) else 'Stocks')
        base_ticker = get_base_ticker(symbol) if is_opt else symbol
        if base_ticker is None: base_ticker = symbol
        if is_opt:
            base_ticker = _aliases.get(base_ticker.upper(), base_ticker)
        
        # If the gain entry is missing currency, bucket it under '?' rather
        # than silently mis-attributing to CAD. The engine populates this
        # field for every Canada/USA gain, so missing means upstream parser
        # didn't set it on the source transaction.
        currency = tx.currency or '?'
        
        if base_ticker not in ticker_stats:
            ticker_stats[base_ticker] = {}
        if currency not in ticker_stats[base_ticker]:
            ticker_stats[base_ticker][currency] = {
                'cap': 0.0, 'opt': 0.0, 'div': 0.0, 'pil': 0.0, 'cost': 0.0, 'proceeds': 0.0,
                'hold_days': [], 'trade_count': 0
            }
        
        tick_stats = ticker_stats[base_ticker][currency]
        
        if tx.action == 'DIVIDEND':
            div = float(tx.dividend or 0)
            tick_stats['div'] += div
        elif tx.action == 'DIVIDEND_IN_LIEU':
            # PIL: ticker-associated but tax-bucketed as ordinary income, not
            # eligible dividend. Separate column so it contributes to total
            # profit without inflating the T5/Schedule B dividend total.
            pil = float(tx.pil or 0)
            tick_stats['pil'] = tick_stats.get('pil', 0.0) + pil
            # Compensation paid on a short (US-INC-04), whatever the
            # security also received: no income reduction (TJ-1060).
            if tx.paid_on_short and pil < 0:
                tick_stats['pil_paid'] = tick_stats.get('pil_paid', 0.0) - pil
            # A Canadian s.260 payment in lieu (a Canadian dealer, a
            # Canadian issuer) is a taxable dividend: the gains run
            # carries its amount as 'dividend' (characterize/income_dating).
            tick_stats['div'] += float(tx.dividend or 0)
        else:
            cost = float(tx.cost or 0)
            proceeds = float(tx.proceeds or 0)
            gain = float(tx.gain or 0)
            days = int(tx.days_held or 0)

            tick_stats['cost'] += cost
            tick_stats['proceeds'] += proceeds
            if is_opt:
                tick_stats['opt'] += gain
            else:
                tick_stats['cap'] += gain

            # Aggregate for statistics
            if asset_type not in returns_by_asset: returns_by_asset[asset_type] = {}
            if currency not in returns_by_asset[asset_type]:
                returns_by_asset[asset_type][currency] = {'dollars': [], 'days': [], 'fees': [], 'fee_shares': []}
            bucket = returns_by_asset[asset_type][currency]
            qty = abs(float(tx.quantity or 0))

            def _trade(dollars, held):
                bucket['dollars'].append(dollars)
                bucket['days'].append(held)
                tick_stats['hold_days'].append(held)
                tick_stats['trade_count'] += 1
                return (len(bucket['dollars']) - 1,
                        len(tick_stats['hold_days']) - 1)

            if tx.grant:
                # A grant-timing WRITE record (s.49(1)) and its buy-back
                # are ONE trade (S040-12: counting both doubled TRADES):
                # the write is the trade, its premium the result until a
                # same-year buy-back folds its cost in below. Leaving the
                # write out (as S040-12 did) dropped every premium from
                # the per-asset block — an expired write vanished and a
                # bought-back one showed only its loss (A2-1114).
                _bi, _hi = _trade(gain, 0)
                open_writes.setdefault(
                    (tx.account or '', symbol, currency), []).append(
                    [bucket, _bi, qty, tick_stats, _hi])
            else:
                # Units of this close that buy back a grant write of the
                # SAME year (the engine's grant_closed; a file written
                # before it: any open write of the series) fold into
                # that write's trade, FIFO. A buy-back of an earlier
                # year's write is a trade of its own.
                fold = 0.0
                if is_opt and tx.direction == 'SHORT':
                    gc = tx.get('grant_closed')
                    if isinstance(gc, dict):
                        _y = str(tx.date_settle or tx.date or '')[:4]
                        fold = sum(float((v or {}).get('units') or 0.0)
                                   for y, v in gc.items() if str(y) == _y)
                    else:
                        fold = qty
                folded = 0.0
                for w in open_writes.get(
                        (tx.account or '', symbol, currency), []):
                    if fold - folded <= 1e-9:
                        break
                    take = min(w[2], fold - folded)
                    if take <= 1e-9:
                        continue
                    w[0]['dollars'][w[1]] += gain * take / qty if qty else gain
                    w[0]['days'][w[1]] = days
                    w[3]['hold_days'][w[4]] = days
                    w[2] -= take
                    folded += take
                rest = qty - folded
                if rest > 1e-9 or qty <= 1e-9:
                    _trade(gain * rest / qty if qty > 1e-9 else gain, days)

            term = tx.term
            if term == 'SHORT_TERM':
                tick_stats['st_gain'] = tick_stats.get('st_gain', 0.0) + gain
            elif term == 'LONG_TERM':
                tick_stats['lt_gain'] = tick_stats.get('lt_gain', 0.0) + gain

        # Track fees if present in the gain entry
        fee = float(tx.fee or 0) + float(tx.commission or 0)
        # != 0, not > 0: fee rebates/corrections arrive negative and
        # must net out of TOTAL TRADING FEES.
        if fee != 0:
            total_fees[currency] = total_fees.get(currency, 0.0) + fee
            if asset_type not in returns_by_asset: returns_by_asset[asset_type] = {}
            if currency not in returns_by_asset[asset_type]: 
                returns_by_asset[asset_type][currency] = {'dollars': [], 'days': [], 'fees': [], 'fee_shares': []}
            
            returns_by_asset[asset_type][currency]['fees'].append(fee)
            returns_by_asset[asset_type][currency]['fee_shares'].append(abs(float(tx.quantity or 0)))
            
            if is_opt:
                option_fees[currency] = option_fees.get(currency, 0.0) + fee

    # If the engine already aggregated total fees across the RAW transaction
    # list (both buy and sell sides), prefer that — gain entries only carry
    # sell-side fees because buy fees are baked into cost basis. The summary
    # total is the right number for trading-efficiency analysis.
    summary_fees = result.summary.get('total_fees_by_currency') or {}
    if summary_fees:
        total_fees = {curr: bucket.get('total', 0.0) for curr, bucket in summary_fees.items()}
        option_fees = {curr: bucket.get('options', 0.0) for curr, bucket in summary_fees.items()}

    return {
        'ticker_stats': ticker_stats,
        'returns_by_asset': returns_by_asset,
        # Dispositions carrying a missing-history OPENING_BALANCE basis.
        # PIPELINE-written gains files carry them in a separate
        # manual_reporting_required section (run_gains strips the rows
        # and pops the flag), so the aggregates above EXCLUDE them;
        # only raw engine output still carries in-line tainted rows.
        # Count both homes so consumers can warn either way
        # (round-five audit: the in-line count was always 0 for
        # pipeline files, silencing `tjs sum`'s warning).
        'tainted_count': sum(1 for t in transactions
                             if t.tainted and 'gain' in t),
        # Pipeline files carry tainted rows OUT-of-line: excluded from
        # every aggregate above, so they get their own count (and the
        # consumer a different warning).
        'tainted_routed': len(result.manual_reporting_required),
        # The routed rows themselves, for the .sum's MANUAL REPORTING
        # section (R1-320: the .sum you file from dropped them without
        # a word while `tjs sum` pointed at a section nobody wrote).
        'manual_rows': [
            # `direction`: a short covered with its short sale before
            # the data lists its cover cost, never as proceeds
            # (form_export.manual_figures, backlog TJ-0497).
            # `own_move_from`: units moved in from another of your
            # accounts, their cost in the blended figures (TJ-0279).
            {k: r.get(k) for k in ('symbol', 'date', 'date_settle',
                                   'qty', 'proceeds', 'account',
                                   'direction', 'own_move_from',
                                   'currency')}
            for r in result.manual_reporting_required],
        'total_fees': total_fees,
        'option_fees': option_fees,
        'total_year': target_year,
        # Carry the wash-sale solver's convergence flag through so the
        # report can warn when the numbers may be unreliable. Default True
        # so engines/old files that don't emit it never spuriously warn.
        'wash_solver_converged': result.summary.get('wash_solver_converged', True),
        'wash_solver_iterations': result.summary.get('wash_solver_iterations'),
    }
