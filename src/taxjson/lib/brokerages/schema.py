"""The normalized-transaction schema, as one declarative table.

Until now the conventions parsers must follow (sign rules, field presence,
SPLIT ratio-in-quantity, symbol_new spelling) lived in scattered comments
and in taxjson_generate_parser's LLM prompt — which had already drifted
from the code. This module is the single source of truth:

  * `SCHEMA` / `KNOWN_ACTIONS` — the per-action field table.
  * `validate_transactions()` — pure validator; taxjson-brokerage runs it
    on every parse (warnings by default, errors fatal under --strict).
  * `render_schema_prompt()` — the schema block taxjson-generate-parser
    embeds in its LLM prompt, generated from the same table so docs,
    scaffolding, and enforcement cannot diverge again.

Convention notes encoded here (the "why" behind the checks):
  * Trade rows (BUYSELL/ASSIGN): `net_amount` is POSITIVE; the trade's
    direction lives in the SIGN OF `quantity` (+buy/-sell). A sell may
    net negative (commission larger than the gross of a penny close).
  * Income rows (DIVIDEND/TAX/INTEREST/...): sign-preserving. Brokers
    (IB especially) post re-characterizations as a negative reversal row
    plus a corrected row; the negatives must flow through so the pair
    nets out. A negative income amount is therefore NOT an error.
  * SPLIT: `quantity` holds the RATIO (2.0 = 2-for-1), not a share
    count; `symbol_new` empty means a plain split, a different symbol
    means a rename/migration. The canonical plain-split spelling is
    `symbol_new=''`; `symbol_new == symbol` is a tolerated legacy
    spelling (IB emits it) that every consumer normalizes via
    `corporate_timeline.normalize_symbol_new` — it is flagged only in
    lint mode, since warning on every IB split would be pure noise.
  * ADJUST: signed ACB delta in `net_amount` (negative = reduction,
    e.g. return of capital).
  * FEE: `net_amount` POSITIVE = charged, NEGATIVE = a refund or
    rebate — the sign IB, Questrade, RBC, the .tt FEE line, taxjson
    fx-cash and the fees report use (a broker's cash sign is the
    opposite: flip it, as TAX is flipped). Audit S065-10.
  * date/date_settle: `YYYY-MM-DD`. `date_settle` is the settlement
    date; equities follow the era-aware T+2 (pre-2024-05-28 US /
    05-27 CA) → T+1 rule, options T+1 — see
    `BaseBrokerage.equity_settlement_date` / `settlement_date_t1`.
    Crypto and other round-the-clock assets settle on the TRADE date
    (date_settle = date): a T+1 helper moved a Dec-31 crypto sale into
    the next tax year (audit S065-13).
"""

import re
from typing import Any, Dict, List, Tuple

from taxjson.lib.core import is_option_symbol

# Actions a parser may emit. (DISALLOW is engine-internal and OPENING_BALANCE
# comes from .tt starting-position files, but both are legal in a transaction
# stream a validator may see.)
KNOWN_ACTIONS = frozenset({
    'BUYSELL', 'ASSIGN', 'SPLIT', 'DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX',
    'INTEREST', 'FEE', 'TRANSFER', 'ADJUST', 'OPENING_BALANCE', 'DISALLOW',
})

# Per-action field table: which fields MUST be present-and-meaningful and
# which are conventional extras. Everything else on TaxTransaction (time,
# account, description, type, id, ...) is universally optional.
SCHEMA: Dict[str, Dict[str, Tuple[str, ...]]] = {
    'BUYSELL':          {'required': ('date', 'symbol', 'quantity', 'currency', 'net_amount'),
                         'optional': ('price', 'commission', 'fee', 'date_settle', 'gross_amount',
                                      'multiplier')},
    'ASSIGN':           {'required': ('date', 'symbol', 'quantity', 'currency'),
                         'optional': ('price', 'net_amount', 'date_settle')},
    'SPLIT':            {'required': ('date', 'symbol', 'quantity'),
                         'optional': ('symbol_new',)},
    'DIVIDEND':         {'required': ('date', 'symbol', 'currency', 'net_amount'),
                         'optional': ('gross_amount', 'quantity', 'price', 'type')},
    'DIVIDEND_IN_LIEU': {'required': ('date', 'symbol', 'currency', 'net_amount'),
                         'optional': ('gross_amount', 'quantity', 'price', 'type')},
    'TAX':              {'required': ('date', 'currency', 'net_amount'),
                         'optional': ('symbol', 'type')},
    'INTEREST':         {'required': ('date', 'currency', 'net_amount'),
                         'optional': ('symbol', 'type')},
    'FEE':              {'required': ('date', 'currency', 'net_amount'),
                         'optional': ('symbol', 'type')},
    'TRANSFER':         {'required': ('date', 'symbol', 'quantity'),
                         'optional': ('currency', 'net_amount')},
    'ADJUST':           {'required': ('date', 'symbol', 'net_amount'),
                         'optional': ('currency', 'type')},
    'OPENING_BALANCE':  {'required': ('date', 'symbol', 'quantity'),
                         'optional': ('currency', 'net_amount')},
    'DISALLOW':         {'required': ('date', 'symbol', 'net_amount'),
                         'optional': ()},
}

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# Market suffixes the toolkit understands. Bare symbols (no dot) are
# legitimate crypto assets and are not suffix-checked.
# The Canadian venues are the one shared set (income_dating; .VN was
# Canadian to the parsers but "unknown" here — audit A2-1077).
from taxjson.lib.income_dating import CA_LISTING_SUFFIXES as _CA_VENUES
KNOWN_SUFFIXES = frozenset({'US', 'AX', 'L'}) | _CA_VENUES

_QTY_EPS = 1e-9
# Futures symbol prefixes (lib/futures.py).
_FUTURES_PREFIXES = ('F:', '/', '\\')
_MONEY_EPS = 0.005


# Leading tag of a WARNING the caller must put on the console (the run's
# ATTENTION channel, `warning: ATTENTION:`), not only in the .sum: a
# notional gap on a row whose multiplier the parser did not declare is
# either a reinvestment priced in another currency (Questrade/RBC
# `REINV@U$` on a CAD row) or wrong money booked with rc 0 (a 10x
# Proceeds on Webull/RBC) — audit S065-12, owner decision: ATTENTION,
# not an error (the futures guess of 1 or 100 made it unfit for one).
ATTENTION_TAG = "ATTENTION: "


def _who(tx: Dict[str, Any], i: int) -> str:
    return (f"tx[{i}] {tx.get('action', '?')} {tx.get('symbol', '?')} "
            f"{tx.get('date', '?')}")


def validate_transactions(txs: List[Dict[str, Any]],
                          lint: bool = False) -> Tuple[List[str], List[str]]:
    """Validate parser-output transaction dicts against the schema.

    Returns (errors, warnings). Errors are convention violations that
    will corrupt downstream math (unknown action, malformed dates, a
    negative trade net, a non-positive split ratio, missing required
    fields, and a trade notional far from qty*price*multiplier when the
    parser DECLARED the row's `multiplier`). Warnings are
    suspicious-but-survivable (unknown market suffix, the same notional
    gap on a row whose multiplier is only guessed). `lint=True` adds
    style-level findings (non-canonical symbol_new spelling) that are
    deliberately silent in everyday runs.
    """
    errors: List[str] = []
    warnings: List[str] = []

    for i, tx in enumerate(txs):
        action = (tx.get('action') or '').upper()
        if action not in KNOWN_ACTIONS:
            errors.append(f"{_who(tx, i)}: unknown action {tx.get('action')!r}")
            continue
        spec = SCHEMA[action]

        # Required fields: present and non-empty (strings) / provided
        # (numerics — 0 is a legal value only where it isn't the ratio
        # or the traded quantity, which get their own checks below).
        for field in spec['required']:
            v = tx.get(field, None)
            if v is None or (isinstance(v, str) and not v.strip()):
                errors.append(f"{_who(tx, i)}: missing required field "
                              f"{field!r}")

        for field in ('date', 'date_settle'):
            v = tx.get(field)
            if v and not _DATE_RE.match(str(v)):
                errors.append(f"{_who(tx, i)}: {field}={v!r} is not "
                              f"YYYY-MM-DD")

        qty = float(tx.get('quantity') or 0.0)
        net = float(tx.get('net_amount') or 0.0)
        price = float(tx.get('price') or 0.0)

        if action in ('BUYSELL', 'ASSIGN'):
            if action == 'BUYSELL' and abs(qty) < _QTY_EPS:
                errors.append(f"{_who(tx, i)}: BUYSELL with quantity 0 "
                              f"(direction lives in the quantity sign)")
            # A SELL may net negative: closing an option at 0.01 with a
            # 1.00+ commission brings in less than nothing, and the
            # engine books those negative proceeds (core._trade_money).
            # Refusing them failed every `taxjson run` on a routine
            # penny close (audit S017-00). A negative BUY is still wrong.
            _sym = str(tx.get('symbol') or '')
            _fut = _sym.startswith(_FUTURES_PREFIXES)
            # A plain-futures BUY at a negative price RECEIVES cash: its
            # signed (negative) cost is the right money, and the
            # magnitude this rule used to force booked the loss as a
            # gain (audit A2-0302).
            _neg_fut_buy = (_fut and not is_option_symbol(_sym)
                            and price < -_MONEY_EPS)
            if (net < -_MONEY_EPS and not qty < -_QTY_EPS
                    and not _neg_fut_buy):
                errors.append(f"{_who(tx, i)}: trade net_amount must be "
                              f">= 0 (got {net}); direction belongs in "
                              f"the quantity sign")
            # A futures price can be negative (WTI, April 2020; a
            # calendar spread): booked correctly, so not an error
            # there (audit S053-13) — whichever futures prefix the
            # symbol carries (F:, / or \, A2-1087/A2-1089).
            if price < -_MONEY_EPS and not _fut:
                errors.append(f"{_who(tx, i)}: negative price {price}")
            # Notional sanity: net_amount vs qty * price * multiplier
            # (brokers round, and fees sit inside net for buys / outside
            # for sells). price==0 rows (crypto awaiting fill-crypto,
            # expiries) are exempt. A row whose parser DECLARES its
            # contract size (`multiplier`: IB carries the statement's
            # own — CL 1000, MET 0.1, equity option 100) is checked
            # against it and a mismatch is an ERROR: the guess of 1 or
            # 100 drowned every futures row in false positives, so the
            # check could only ever warn. Rows without a declared
            # multiplier keep the guess and stay warn-level.
            # An ASSIGN leg that carries a price (the stock leg at the
            # strike) is checked too, net 0 included: a wrong or blank
            # Proceeds on it booked silently, even a negative ACB (audit
            # S017-02). A zero-price leg (the option side) is exempt.
            _declared_raw = tx.get('multiplier')
            try:
                _has_size = float(_declared_raw or 0.0) > 0
            except (TypeError, ValueError):
                _has_size = False
            # A futures contract size is never guessed: the 1 (or 100)
            # guess put an ATTENTION on every generic-importer futures
            # row (audit A2-1082). Checked only when declared.
            if price > 0 and (net > 0 if action == 'BUYSELL'
                              else abs(qty) > _QTY_EPS) \
                    and not (_fut and not (_has_size
                                           and action == 'BUYSELL')):
                declared = tx.get('multiplier')
                try:
                    declared = float(declared) if declared else 0.0
                except (TypeError, ValueError):
                    declared = 0.0
                if action == 'ASSIGN':
                    declared = 0.0          # warn-level: never declared
                if declared > 0:
                    mult = declared
                else:
                    mult = (100.0 if is_option_symbol(tx.get('symbol') or '')
                            else 1.0)
                expected = abs(qty) * price * mult
                fees = (abs(float(tx.get('commission') or 0.0))
                        + abs(float(tx.get('fee') or 0.0)))
                gap = abs(expected - abs(net))
                if gap > fees + max(5.0, 0.02 * expected):
                    msg = (f"{_who(tx, i)}: net_amount {net:,.2f} is far "
                           f"from qty*price"
                           f"{f'*{mult:g}' if mult != 1 else ''} = "
                           f"{expected:,.2f} (±fees {fees:,.2f})")
                    if declared > 0:
                        errors.append(f"{msg} — possible data typo; "
                                      f"silent wrong money if real")
                    else:
                        warnings.append(
                            f"{ATTENTION_TAG}{msg} — the booked cost or "
                            f"proceeds is net_amount: check the row (a "
                            f"price in another currency, e.g. a DRIP "
                            f"REINV@U$ on a CAD row, is harmless; a "
                            f"wrong Proceeds/Value column is wrong money)")

        elif action == 'SPLIT':
            if qty <= 0:
                errors.append(f"{_who(tx, i)}: SPLIT ratio (quantity) must "
                              f"be > 0, got {qty} — a 0 ratio wipes the "
                              f"pool")
            if lint:
                new = tx.get('symbol_new') or ''
                if new and new == tx.get('symbol'):
                    warnings.append(
                        f"{_who(tx, i)}: symbol_new == symbol is the "
                        f"legacy plain-split spelling; canonical is "
                        f"symbol_new='' (both are normalized downstream)")

        # A bare (crypto) symbol settles on its trade date; a later
        # settle date is an equity/option cycle applied to a 24/7 asset
        # (a Dec-31 sale then lands in the next year — S065-13).
        _bare = tx.get('symbol') or ''
        if (action == 'BUYSELL' and _bare and '.' not in _bare
                and not _bare.startswith('F:')
                and not is_option_symbol(_bare)
                and tx.get('date_settle') and tx.get('date')
                and tx['date_settle'] != tx['date']):
            warnings.append(f"{_who(tx, i)}: bare (crypto) symbol settles "
                            f"{tx['date_settle']}, not on its trade date "
                            f"— crypto has no settlement cycle")

        # An option EXPIRY (a no-money BUYSELL leg) is dated its expiry
        # day: one that expires Dec 31 and "settles" Jan 2 moves its loss
        # to the next year (CA-DATE-08 / US-DATE-08; the twin of the
        # crypto check, audit A2-1088). A trade or an exercise on the
        # expiry day really settles T+1 (CA-DATE-04) and is not flagged.
        if action == 'BUYSELL' and tx.get('date_settle') \
                and abs(price) < _MONEY_EPS and abs(net) < _MONEY_EPS \
                and is_option_symbol(_bare):
            from taxjson.lib.core import parse_option_expiry
            _exp = parse_option_expiry(_bare)
            if _exp and str(tx['date_settle']) > _exp:
                warnings.append(f"{_who(tx, i)}: the option expiry "
                                f"settles {tx['date_settle']}, after the "
                                f"expiry day {_exp} — an expiry is dated "
                                f"its expiry day")

        # Suffix sanity: dotted symbols must end in a known market
        # suffix (bare symbols are crypto and exempt). OCC option
        # symbols carry their own suffix and match the core regex.
        symbol = tx.get('symbol') or ''
        if '.' in symbol and not is_option_symbol(symbol):
            ext = symbol.rsplit('.', 1)[1].upper()
            if ext not in KNOWN_SUFFIXES:
                warnings.append(f"{_who(tx, i)}: symbol suffix .{ext} is "
                                f"not a known market suffix "
                                f"({', '.join(sorted(KNOWN_SUFFIXES))})")

    return errors, warnings


def render_schema_prompt() -> str:
    """The transaction-schema block for taxjson-generate-parser's LLM
    prompt — generated from SCHEMA so the scaffold can't drift from the
    validator again."""
    lines = [
        "Each transaction is a JSON object. Emit ONLY these actions: "
        + ", ".join(sorted(a for a in KNOWN_ACTIONS if a != 'DISALLOW'))
        + ".",
        "",
        "Universal fields: date (YYYY-MM-DD), time (HH:MM:SS, default "
        "09:30:00), account, description (keep the raw broker text — "
        "security-override matching keys on it), currency.",
        "",
        "Per-action required fields:",
    ]
    for action in sorted(SCHEMA):
        if action == 'DISALLOW':
            continue
        spec = SCHEMA[action]
        lines.append(f"  {action}: requires {', '.join(spec['required'])}"
                     + (f"; optional {', '.join(spec['optional'])}"
                        if spec['optional'] else ""))
    lines += [
        "",
        "Conventions (violations corrupt tax math downstream):",
        "  - Trades (BUYSELL/ASSIGN): net_amount is POSITIVE; the "
        "direction is the SIGN of quantity (+buy / -sell). net_amount is "
        "fee-inclusive for buys, net of fees for sells (a sell whose fee "
        "exceeds its gross nets negative).",
        "  - multiplier (optional, BUYSELL): the contract size when the "
        "export states it (100 per equity option, 1000 per CL future); "
        "a declared multiplier makes |net - qty*price*multiplier| beyond "
        "fees a schema ERROR.",
        "  - Income rows are SIGN-PRESERVING: brokers post negative "
        "reversal rows that must net out — never abs() an amount.",
        "  - DIVIDEND gross_amount is the pre-withholding amount when "
        "known; TAX rows are positive-means-withheld.",
        "  - FEE: net_amount POSITIVE = charged, NEGATIVE = a refund or "
        "rebate (flip the broker's cash sign; never abs()).",
        "  - SPLIT: quantity holds the RATIO (2.0 = 2-for-1). "
        "symbol_new='' for a plain split; a different symbol means a "
        "rename.",
        "  - ADJUST: signed ACB delta in net_amount (negative = "
        "reduction, e.g. return of capital — use "
        "BaseBrokerage.tx_roc_adjust for rows whose description says "
        "RETURN OF CAPITAL).",
        "  - date_settle: use the broker's settlement column when "
        "present; otherwise compute it — equities are era-aware T+2 "
        "(before 2024-05-28 US / 2024-05-27 CA) then T+1: call "
        "BaseBrokerage.equity_settlement_date (do NOT hardcode T+1 for "
        "equities); options are T+1: settlement_date_t1. Crypto and "
        "other round-the-clock assets settle on the TRADE date "
        "(date_settle = date) — no settle helper applies to them.",
        "  - Equity symbols carry a market suffix from the currency "
        "(.US/.TO/...) via BaseBrokerage.apply_currency_suffix; option "
        "symbols are OCC format (BASE + yymmdd + C/P + 8-digit strike) "
        "via format_occ_symbol.",
        "  - Rows you do not handle must be COUNTED, not silently "
        "dropped: call self.count_skip('<category>') and let "
        "emit_skip_summary report them.",
    ]
    return "\n".join(lines)
