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
  * Trade rows (BUYSELL/ASSIGN): `net_amount` is ALWAYS POSITIVE; the
    trade's direction lives in the SIGN OF `quantity` (+buy/-sell).
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
  * date/date_settle: `YYYY-MM-DD`. `date_settle` is the settlement
    date; equities follow the era-aware T+2 (pre-2024-05-28 US /
    05-27 CA) → T+1 rule, options T+1 — see
    `BaseBrokerage.equity_settlement_date` / `settlement_date_t1`.
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
                         'optional': ('price', 'commission', 'fee', 'date_settle', 'gross_amount')},
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
KNOWN_SUFFIXES = frozenset({'TO', 'US', 'AX', 'L', 'V', 'CN', 'NE'})

_QTY_EPS = 1e-9
_MONEY_EPS = 0.005


def _who(tx: Dict[str, Any], i: int) -> str:
    return (f"tx[{i}] {tx.get('action', '?')} {tx.get('symbol', '?')} "
            f"{tx.get('date', '?')}")


def validate_transactions(txs: List[Dict[str, Any]],
                          lint: bool = False) -> Tuple[List[str], List[str]]:
    """Validate parser-output transaction dicts against the schema.

    Returns (errors, warnings). Errors are convention violations that
    will corrupt downstream math (unknown action, malformed dates, a
    negative trade net, a non-positive split ratio, missing required
    fields). Warnings are suspicious-but-survivable (unknown market
    suffix, trade notional far from qty*price). `lint=True` adds
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
            if net < -_MONEY_EPS:
                errors.append(f"{_who(tx, i)}: trade net_amount must be "
                              f">= 0 (got {net}); direction belongs in "
                              f"the quantity sign")
            if price < -_MONEY_EPS:
                errors.append(f"{_who(tx, i)}: negative price {price}")
            # Notional sanity (warn-level: brokers round, and fees sit
            # inside net for buys / outside for sells). price==0 rows
            # (crypto awaiting fill-crypto, expiries) are exempt.
            if action == 'BUYSELL' and price > 0 and net > 0:
                mult = 100.0 if is_option_symbol(tx.get('symbol') or '') else 1.0
                expected = abs(qty) * price * mult
                fees = (abs(float(tx.get('commission') or 0.0))
                        + abs(float(tx.get('fee') or 0.0)))
                gap = abs(expected - net)
                if gap > fees + max(5.0, 0.02 * expected):
                    warnings.append(
                        f"{_who(tx, i)}: net_amount {net:,.2f} is far from "
                        f"qty*price{'*100' if mult > 1 else ''} = "
                        f"{expected:,.2f} (±fees {fees:,.2f}) — possible "
                        f"data typo; silent wrong money if real")

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
        "  - Trades (BUYSELL/ASSIGN): net_amount is ALWAYS POSITIVE; the "
        "direction is the SIGN of quantity (+buy / -sell). net_amount is "
        "fee-inclusive for buys, net of fees for sells.",
        "  - Income rows are SIGN-PRESERVING: brokers post negative "
        "reversal rows that must net out — never abs() an amount.",
        "  - DIVIDEND gross_amount is the pre-withholding amount when "
        "known; TAX rows are positive-means-withheld.",
        "  - SPLIT: quantity holds the RATIO (2.0 = 2-for-1). "
        "symbol_new='' for a plain split; a different symbol means a "
        "rename.",
        "  - ADJUST: signed ACB delta in net_amount (negative = "
        "reduction, e.g. return of capital — use "
        "BaseBrokerage.tx_roc_adjust for rows whose description says "
        "RETURN OF CAPITAL).",
        "  - date_settle: use the broker's settlement column when "
        "present; otherwise compute it — equities are era-aware T+2 "
        "(before 2024-05-28 US / 2024-05-27 CA) then T+1, options are "
        "T+1. Call BaseBrokerage.equity_settlement_date / "
        "settlement_date_t1; do NOT hardcode T+1 for equities.",
        "  - Equity symbols carry a market suffix from the currency "
        "(.US/.TO/...) via BaseBrokerage.apply_currency_suffix; option "
        "symbols are OCC format (BASE + yymmdd + C/P + 8-digit strike) "
        "via format_occ_symbol.",
        "  - Rows you do not handle must be COUNTED, not silently "
        "dropped: call self.count_skip('<category>') and let "
        "emit_skip_summary report them.",
    ]
    return "\n".join(lines)
