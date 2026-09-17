#!/usr/bin/env python3
"""
taxjson_convert_tt.py

Bi-directional conversion between TaxText (.tt) and taxjson (.json).

Usage:
    taxjson-convert-tt input.tt                # → JSON to stdout
    taxjson-convert-tt input.tt out.json       # → JSON to file
    taxjson-convert-tt input.json              # → tt to stdout
    taxjson-convert-tt input.json out.tt       # → tt to file

Direction is inferred from the input file extension. The legacy `.tt`
format is a single space-separated line per transaction; see the legacy
Perl scripts (cb_trades.pl, kr_ledgers.pl) for the exact field order.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

_VALID_ACTIONS = (
    'BUYSELL', 'TRANSFER', 'SPLIT', 'ASSIGN', 'ADJUST', 'DISALLOW',
    'DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE',
)


def parse_tt_line(line: str, account_name: str = 'default'):
    parts = line.split()
    if not parts or parts[0].startswith('#'):
        return None

    action = parts[0]
    if action not in _VALID_ACTIONS:
        return None

    # Any short-row IndexError or bad-numeric ValueError below is a
    # malformed `.tt` row. Raise loudly with the offending line so the
    # user can hand-fix it — silent skip would lose a tax event.
    try:
        tx = {
            'action': action,
            'date': parts[1],
            'time': parts[2] if len(parts) > 2 and ':' in parts[2] else '09:30:00',
            'date_settle': parts[1],
            'account': account_name,
        }

        if action in ('BUYSELL', 'ASSIGN', 'TRANSFER', 'SPLIT'):
            tx['symbol'] = parts[3]
            if action == 'SPLIT':
                tx['symbol_new'] = parts[4]
                tx['quantity'] = float(parts[5].replace(',', ''))
                tx['currency'] = 'CAD'
                tx['price'] = 0.0
                tx['net_amount'] = 0.0
            else:
                tx['quantity'] = float(parts[4].replace(',', ''))
                tx['currency'] = parts[5]
                tx['price'] = float(parts[6].replace(',', ''))
                tx['net_amount'] = float(parts[7].replace(',', ''))
                if action != 'TRANSFER':
                    tx['fee'] = float(parts[8].replace(',', '')) if len(parts) > 8 else 0.0
                elif 'DECLARED' in parts[8:]:
                    # Token accepted anywhere past the core 8 fields:
                    # legacy hand-written rows sometimes carry a
                    # trailing fee-style 0.00000 column before it.
                    # Opt-in declaration token: THIS transfer is a
                    # deliberate user statement (custody-move counter /
                    # attestation), so the pipeline's near-trade
                    # netting refusal stands down for its zero-net
                    # cluster. Opt-in, NOT every .tt TRANSFER: .tt-only
                    # books also record genuine in-kind moves as
                    # TRANSFER rows, and a json→tt→json round trip
                    # must not silently grant broker rows attestation
                    # (2026-09 round-four audit).
                    from taxjson.lib.pipeline import \
                        MANUAL_TRANSFER_DECLARATION
                    tx['description'] = MANUAL_TRANSFER_DECLARATION
                # Sanity-check the hand-entered total against qty*price±fee
                # (buy = qty*price + fee; sell = qty*price - fee). The engine
                # uses net_amount directly as cost/proceeds, so a one-
                # keystroke typo here is silent wrong money. Warn, don't
                # fail: odd lots / rounding / FX-inclusive totals can differ
                # legitimately by a little — 1% + $0.05 tolerance.
                _q = tx['quantity']
                _fee = tx.get('fee', 0.0)
                _expected = abs(_q) * tx['price'] + (_fee if _q > 0 else -_fee)
                _total = abs(tx['net_amount'])
                if (tx['price'] > 0 and abs(_q) > 0
                        and abs(_total - _expected) >
                        max(0.05, 0.01 * max(_expected, 1.0))):
                    print(
                        f"warning: .tt line total {_total:.2f} differs from "
                        f"qty*price{'+' if _q > 0 else '-'}fee = "
                        f"{_expected:.2f} by more than 1%: {line.strip()!r} "
                        f"— check for a typo (the total IS what the engine "
                        f"books as cost/proceeds).",
                        file=sys.stderr,
                    )

        elif action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'):
            tx['symbol'] = parts[3]
            tx['quantity'] = float(parts[4].replace(',', ''))
            tx['currency'] = parts[5]
            val = float(parts[7].replace(',', ''))
            tx['gross_amount'] = val
            # Optional 9th column: the withholding-NETTED amount (the
            # emitter writes it when net != gross so a round-trip
            # doesn't inflate income to the gross figure).
            tx['net_amount'] = (float(parts[8].replace(',', ''))
                                if len(parts) > 8 else val)
            tx['type'] = action.lower()

        elif action == 'INTEREST' or action == 'FEE':
            tx['currency'] = parts[3]
            tx['net_amount'] = float(parts[4].replace(',', ''))
            tx['type'] = action.lower()
            tx['symbol'] = 'CASH'

        elif action == 'ADJUST' or action == 'DISALLOW':
            tx['symbol'] = parts[3]
            tx['currency'] = parts[4]
            tx['net_amount'] = float(parts[5].replace(',', ''))
    except (ValueError, IndexError) as e:
        raise ValueError(
            f"taxjson-convert-tt: malformed .tt line ({type(e).__name__}: "
            f"{e}). Action={action!r}, line={line!r}. Hand-edit or remove "
            f"the row before re-running — silent skip would lose a "
            f"transaction the engine downstream needs."
        ) from e

    tx['id'] = compute_tt_id(tx)
    return tx


def compute_tt_id(tx: dict) -> str:
    """The row's content-hash id — the same field set and formatting as
    `TaxTransaction.compute_id` so a `.tt`-sourced row and a JSON-native
    load of the same content agree. Recomputed by tt_to_json after
    split-fill disambiguation rewrites `description`."""
    # Use `repr(float(...))` for monetary fields, matching
    # `TaxTransaction.compute_id` (`core.py:38`). The earlier
    # `:.8f` formatting truncated below satoshi precision and
    # caused a `.tt` → JSON round-trip to compute a different id
    # than a JSON-native load — breaking dedup and wash-linkage
    # for sub-satoshi crypto quantities.
    components = [
        tx.get('action', ''),
        tx.get('date', ''),
        tx.get('time', ''),
        tx.get('symbol', ''),
        repr(float(tx.get('quantity', 0.0))),
        tx.get('currency', ''),
        repr(float(tx.get('price', 0.0))),
        repr(float(tx.get('net_amount', 0.0))),
        repr(float(tx.get('gross_amount', 0.0))),
        tx.get('account', 'default'),
        tx.get('type', ''),
        tx.get('date_settle', ''),
        tx.get('description', ''),
    ]
    raw_id = "|".join(str(c) for c in components)
    return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]


def tx_to_tt_line(tx: dict):
    """Emit a single `.tt` line for one transaction, matching the legacy
    Perl scripts' field order. Returns None for transactions whose action
    has no `.tt` representation (e.g. OPENING_BALANCE) so the caller can
    skip them. Numeric fields use %.8f for qty/price, %.5f for totals/fees
    (the convention from cb_trades.pl / kr_ledgers.pl)."""
    action = tx.get('action', '')
    if action not in _VALID_ACTIONS:
        return None

    date = tx.get('date', '')
    time = tx.get('time', '09:30:00')
    symbol = tx.get('symbol', '')
    qty = float(tx.get('quantity') or 0.0)
    currency = tx.get('currency') or 'CAD'
    price = float(tx.get('price') or 0.0)
    net = float(tx.get('net_amount') or 0.0)
    # Dividend/tax tt rows quote the gross (pre-withholding) amount, so
    # prefer it when present — parsers leave gross at 0 when only net is
    # known, in which case net is the right fallback.
    gross = float(tx.get('gross_amount') or 0.0) or net
    fee = float(tx.get('fee') or 0.0)

    if action in ('BUYSELL', 'ASSIGN'):
        # ACTION date time symbol qty currency price total fee
        # SIGNED total/fee: abs() re-inflated sign-preserved reversal
        # rows (and fee rebates) on a json→tt→json cycle — the exact
        # corruption the parser-level sign fixes removed. Direction
        # still comes from qty; a negative total is a reversal.
        return f"{action} {date} {time} {symbol} {qty:.8f} {currency} {price:.8f} {net:.5f} {fee:.5f}"

    if action == 'TRANSFER':
        line = (f"TRANSFER {date} {time} {symbol} {qty:.8f} {currency} "
                f"{price:.8f} {net:.5f}")
        # Round-trip the opt-in declaration token: without it a
        # json→tt→json cycle would strip attestation from declared
        # rows (and could never re-grant it, by design).
        from taxjson.lib.pipeline import MANUAL_TRANSFER_DECLARATION
        if tx.get('description') == MANUAL_TRANSFER_DECLARATION:
            line += " DECLARED"
        return line

    if action == 'SPLIT':
        # SPLIT date time symbol_old symbol_new ratio
        symbol_new = tx.get('symbol_new') or symbol
        return f"SPLIT {date} {time} {symbol} {symbol_new} {qty:.8f}"

    if action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'):
        # ACTION date time symbol qty currency price total
        # SIGNED total: a broker reversal/refund row is negative and must
        # round-trip — abs() here re-inflated income/tax on json→tt→json
        # (the exact bug the sign-preserving IB parser fix removed), and the
        # changed net_amount hashed to a different id, breaking dedup
        # against the originals. The .tt parser reads the value signed.
        line = (f"{action} {date} {time} {symbol} {qty:.8f} {currency} "
                f"{price:.8f} {gross:.5f}")
        # Optional 9th column: the withholding-NETTED amount, emitted
        # only when it differs from gross — without it a json→tt→json
        # cycle re-parsed net as gross and inflated income. Legacy
        # positional consumers ignore trailing columns.
        if abs(net - gross) > 0.005:
            line += f" {net:.5f}"
        return line

    if action in ('INTEREST', 'FEE'):
        # ACTION date time currency amount  (sign preserved on interest)
        return f"{action} {date} {time} {currency} {net:.5f}"

    if action in ('ADJUST', 'DISALLOW'):
        # ACTION date time symbol currency amount
        return f"{action} {date} {time} {symbol} {currency} {net:.5f}"

    return None


def expand_acquired(line: str):
    """`ACQUIRED` sugar — one line for the lost-history custody idiom.

        ACQUIRED <true-date> <time> <sym> <qty> <cur> <price> <total> \
            ARRIVED <arrival-date>

    expands to the two rows the AmbiguousTransferDateError resolution
    prescribes: a BUYSELL dated the TRUE acquisition day (establishing
    the balance at real cost) plus a DECLARED counter-TRANSFER dated
    the broker's arrival day (netting the arrival leg out). Returns
    None when the line is not an ACQUIRED line; raises loudly on a
    malformed one — silent skip would lose the declared history."""
    parts = line.split()
    if not parts or parts[0] != 'ACQUIRED':
        return None
    if len(parts) < 10 or parts[8] != 'ARRIVED':
        raise ValueError(
            f"taxjson-convert-tt: malformed ACQUIRED line — expected "
            f"`ACQUIRED <true-date> <time> <sym> <qty> <cur> <price> "
            f"<total> ARRIVED <arrival-date>`, got: {line.strip()!r}")
    _, tdate, ttime, sym, qty, cur, price, total = parts[:8]
    arrival = parts[9]
    qty = qty.replace(',', '')
    if float(qty) <= 0:
        raise ValueError(
            f"taxjson-convert-tt: ACQUIRED quantity must be positive "
            f"(it declares shares you HOLD): {line.strip()!r}")
    return [
        f"BUYSELL {tdate} {ttime} {sym} {qty} {cur} {price} {total} 0.0",
        # Counter-TRANSFER at the FIXED default time: the engine's
        # error message prescribes the 09:30:00 idiom, and the two
        # must hash to identical ids so a hand-written pair and an
        # ACQUIRED expansion dedup as one (round-six audit).
        f"TRANSFER {arrival} 09:30:00 {sym} -{qty} {cur} {price} "
        f"{total} DECLARED",
    ]


def tt_to_json(input_path: Path, account_name: str) -> dict:
    transactions = []
    with input_path.open('r', encoding='utf-8') as f:
        for line in f:
            expanded = expand_acquired(line)
            for one in (expanded if expanded is not None else [line]):
                tx = parse_tt_line(one, account_name=account_name)
                if tx:
                    transactions.append(tx)
    # Per-file split-fill disambiguation, exactly as every brokerage
    # parser does: two byte-identical hand-entered lines (one order
    # filled in two pieces at the same price; the default 09:30:00
    # time gives no sub-day resolution) hashed to the same id and
    # collapsed to ONE trade under `taxjson run`'s always-on --dedup
    # (stage-tools audit). The 2nd+ instance gets a `[fill #N]`
    # description marker and its id is recomputed from the marked
    # content; the first keeps its stable id. DECLARED counter-
    # transfers are exempt: the pipeline recognises the declaration by
    # EXACT description equality, and the ACQUIRED expansion relies on
    # a hand-written pair hashing identically.
    from taxjson.lib.brokerages.base import BaseBrokerage
    from taxjson.lib.pipeline import MANUAL_TRANSFER_DECLARATION
    BaseBrokerage.disambiguate_split_fills(
        [tx for tx in transactions
         if tx.get('description') != MANUAL_TRANSFER_DECLARATION])
    for tx in transactions:
        tx['id'] = compute_tt_id(tx)
    return {
        "transactions": transactions,
        "metadata": {
            "source_file": str(input_path),
            "converted_by": "taxjson_convert_tt.py",
            # Lets fee attribution (taxjson-fees/fees-sum) count
            # manually-entered trade commissions instead of silently
            # skipping .tt books (2026-09 audit).
            "source_brokerage": "manual (.tt)",
        },
    }


def json_to_tt_lines(input_path: Path):
    with input_path.open('r', encoding='utf-8') as f:
        data = json.load(f)
    transactions = data.get('transactions', data) if isinstance(data, dict) else data
    skipped = 0
    for tx in transactions:
        line = tx_to_tt_line(tx)
        if line is None:
            skipped += 1
            continue
        yield line
    if skipped:
        print(
            f"note: skipped {skipped} transaction(s) with no .tt representation",
            file=sys.stderr,
        )


def main():
    try:
        _main()
    except ValueError as exc:
        # Malformed .tt lines raise deliberately-loud ValueErrors;
        # surface them as clean CLI errors, not tracebacks
        # (2026-09 audit).
        print(f"taxjson-convert-tt: error: {exc}", file=sys.stderr)
        sys.exit(1)


def _main():
    parser = argparse.ArgumentParser(
        description="Bi-directional conversion between .tt and .json. "
                    "Direction is inferred from the input file's extension."
    )
    parser.add_argument("input", help="Input file (.tt or .json)")
    parser.add_argument(
        "output",
        nargs="?",
        help="Output file. Omit to write to stdout.",
    )
    parser.add_argument(
        "--account-name",
        metavar="NAME",
        default="default",
        help=(
            "Account label baked into each tx-id hash (tt→json only). "
            "E.g. 'Margin', 'RRSP', 'TFSA'. (default: 'default')"
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"taxjson-convert-tt: error: input file not found: {input_path}",
              file=sys.stderr)
        sys.exit(1)

    suffix = input_path.suffix.lower()
    if suffix == '.json':
        # JSON → tt
        out_fh = open(args.output, 'w', encoding='utf-8') if args.output else sys.stdout
        try:
            for line in json_to_tt_lines(input_path):
                out_fh.write(line + "\n")
        finally:
            if args.output:
                out_fh.close()
    else:
        # tt → JSON  (default, also handles unknown extensions)
        result = tt_to_json(input_path, args.account_name)
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, sort_keys=True)
        else:
            json.dump(result, sys.stdout, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
