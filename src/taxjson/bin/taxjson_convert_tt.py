#!/usr/bin/env python3
"""
taxjson_convert_tt.py

Bi-directional conversion between TaxText (.tt) and taxjson (.json).

Usage:
    taxjson-convert-tt input.tt                # → JSON to stdout
    taxjson-convert-tt input.tt out.json       # → JSON to file
    taxjson-convert-tt input.json              # → tt to stdout
    taxjson-convert-tt input.json out.tt       # → tt to file

Direction is inferred from the input file extension. The `.tt` format
is a single space-separated line per transaction; the field order of
each action is in parse_tt_line / tx_to_tt_line below (and the README's
`.tt` field table, under "find-missing-history").
"""

import argparse
import difflib
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from taxjson.lib.cli_diag import guard_main

_VALID_ACTIONS = (
    'BUYSELL', 'TRANSFER', 'SPLIT', 'ASSIGN', 'ADJUST', 'DISALLOW',
    'DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE',
)
# Line-level sugar expanded by tt_to_json before parse_tt_line.
_SUGAR_ACTIONS = ('ACQUIRED',)

# Highest token count per action (fields + optional trailing columns).
# A token past these used to be ignored without a word — a stray
# column is a typo to fix (notes belong after `#`).
_MAX_TOKENS = {
    'BUYSELL': 10, 'ASSIGN': 10, 'SPLIT': 6,
    'DIVIDEND': 9, 'DIVIDEND_IN_LIEU': 9, 'TAX': 9,
    'INTEREST': 5, 'FEE': 5, 'ADJUST': 6, 'DISALLOW': 6,
}
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# Futures symbol prefixes (lib/futures.py): the contract size is not on
# a .tt line, so no qty x price comparison is possible.
_FUTURES_PREFIXES = ('F:', '/', '\\')
_TIME_RE = re.compile(r'^\d{2}:\d{2}:\d{2}$')
# Optional contract size after the fee on a BUYSELL/ASSIGN line: `x1000`
# (a CL futures option), `x50` (ES), `x0.1` (a micro crypto future) —
# audit S026-22. Without it an option line is checked at the equity 100
# and a futures line is not checked at all.
_MULT_RE = re.compile(r'^[xX](\d+(?:\.\d+)?)$')


def strip_tt_comment(line: str) -> str:
    """Drop an inline `# ...` comment. Before, `BUYSELL ... # note`
    tokenised the note as data, and a `# DECLARED` remark on a TRANSFER
    silently granted the attestation token."""
    i = line.find('#')
    return line if i < 0 else line[:i]


def _where(source: str) -> str:
    return f"{source}: " if source else ''


def _check_date(tok: str, what: str, line: str, source: str) -> None:
    ok = bool(_DATE_RE.match(tok))
    if ok:
        try:
            datetime.strptime(tok, '%Y-%m-%d')
        except ValueError:
            ok = False
    if not ok:
        raise ValueError(
            f"{_where(source)}{what} {tok!r} is not a valid YYYY-MM-DD "
            f"date: {line.strip()!r}")


def _check_time(tok: str, line: str, source: str) -> None:
    ok = bool(_TIME_RE.match(tok))
    if ok:
        try:
            datetime.strptime(tok, '%H:%M:%S')
        except ValueError:
            ok = False
    if not ok:
        raise ValueError(
            f"{_where(source)}time {tok!r} is not a valid HH:MM:SS time "
            f"(the time column is required; use 09:30:00 when unknown): "
            f"{line.strip()!r}")


def _tt_num(tok: str) -> float:
    """A .tt numeric token. A comma is only a thousands separator
    (`1,234.56`); a decimal comma (`48,24`, `1.234,56`) used to have its
    comma stripped and be read 100x too large (audit R1-118) -- it now
    raises, as do `nan`/`inf` and other non-numbers."""
    from taxjson.lib.brokerages.base import parse_strict_number
    return parse_strict_number(tok, field='number')


def _unknown_action(action: str, line: str, source: str) -> ValueError:
    valid = _VALID_ACTIONS + _SUGAR_ACTIONS
    guess = difflib.get_close_matches(action.upper(), valid, n=1,
                                      cutoff=0.5)
    hint = f" Did you mean {guess[0]}?" if guess else ''
    return ValueError(
        f"{_where(source)}unknown .tt action {action!r}.{hint} Valid "
        f"actions: {', '.join(valid)} (case-sensitive). The row was "
        f"NOT converted — a silently dropped line is a lost trade. "
        f"Line: {line.strip()!r}")


def parse_tt_line(line: str, account_name: str = 'default',
                  source: str = ''):
    """One `.tt` line -> taxjson transaction dict, or None for a blank /
    comment-only line. Raises ValueError on anything else that is not a
    well-formed row: unknown action, bad date/time, short row, bad
    number, stray trailing token. `source` ("file.tt:12") prefixes
    every error and warning."""
    line = strip_tt_comment(line)
    parts = line.split()
    if not parts:
        return None

    action = parts[0]
    if action not in _VALID_ACTIONS:
        raise _unknown_action(action, line, source)
    if len(parts) < 3:
        raise ValueError(
            f"{_where(source)}malformed .tt line — {action} needs at "
            f"least a date and a time: {line.strip()!r}")
    _check_date(parts[1], 'date', line, source)
    _check_time(parts[2], line, source)
    _max = _MAX_TOKENS.get(action)
    if _max is not None and len(parts) > _max:
        raise ValueError(
            f"{_where(source)}{action} row has {len(parts) - _max} "
            f"unexpected trailing token(s) {parts[_max:]} — put notes "
            f"after `#`: {line.strip()!r}")

    # Any short-row IndexError or bad-numeric ValueError below is a
    # malformed `.tt` row. Raise loudly with the offending line so the
    # user can hand-fix it — silent skip would lose a tax event.
    try:
        tx = {
            'action': action,
            'date': parts[1],
            'time': parts[2],
            'date_settle': parts[1],
            'account': account_name,
        }

        if action in ('BUYSELL', 'ASSIGN', 'TRANSFER', 'SPLIT'):
            # Symbols are canonical upper-case, like the currency: a
            # hand-typed `aapl.us` was its own ACB pool and the broker's
            # sale of AAPL.US opened a phantom short (S001-06).
            tx['symbol'] = parts[3].upper()
            if action == 'SPLIT':
                tx['symbol_new'] = parts[4].upper()
                tx['quantity'] = _tt_num(parts[5])
                tx['currency'] = 'CAD'
                tx['price'] = 0.0
                tx['net_amount'] = 0.0
            else:
                _declared = None
                if action in ('BUYSELL', 'ASSIGN'):
                    # The optional `x<size>` token is the LAST one (after
                    # the fee); any other trailing token is refused.
                    if len(parts) > 8 and _MULT_RE.match(parts[-1]):
                        _declared = float(_MULT_RE.match(parts[-1]).group(1))
                        if _declared <= 0:
                            raise ValueError(
                                f"{_where(source)}contract size "
                                f"{parts[-1]!r} must be > 0: "
                                f"{line.strip()!r}")
                        parts = parts[:-1]
                    if len(parts) > 9:
                        raise ValueError(
                            f"{_where(source)}{action} row has 1 "
                            f"unexpected trailing token(s) {parts[9:]} "
                            f"(only a contract size like `x1000` may "
                            f"follow the fee) — put notes after `#`: "
                            f"{line.strip()!r}")
                tx['quantity'] = _tt_num(parts[4])
                tx['currency'] = parts[5].upper()
                tx['price'] = _tt_num(parts[6])
                tx['net_amount'] = _tt_num(parts[7])
                if _declared is not None:
                    tx['multiplier'] = _declared
                if action == 'TRANSFER':
                    # Past the 8 core fields a TRANSFER may carry a
                    # legacy fee-style number and/or the DECLARED token
                    # — nothing else.
                    for _tok in parts[8:]:
                        if _tok == 'DECLARED':
                            continue
                        _tt_num(_tok)
                if action != 'TRANSFER':
                    tx['fee'] = _tt_num(parts[8]) if len(parts) > 8 else 0.0
                elif 'DECLARED' in parts[8:]:
                    # A real TOKEN only: inline comments are stripped
                    # before tokenising, so `# DECLARED` in a remark no
                    # longer grants attestation.
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
                # Option rows: price is per SHARE, the contract covers
                # 100 — without the ×100 every option line warned.
                # Futures: the contract size is not on the line — the
                # 1/100 guess called every correct futures total a typo
                # (S029-00), so no comparison for them.
                from taxjson.lib.core import is_option_symbol
                _is_fut = tx['symbol'].startswith(_FUTURES_PREFIXES)
                _mult = (tx['multiplier'] if tx.get('multiplier')
                         else 100.0 if (is_option_symbol(tx['symbol'])
                                        and not _is_fut)
                         else 1.0)
                _q = tx['quantity']
                _fee = tx.get('fee', 0.0)
                _expected = (abs(_q) * tx['price'] * _mult
                             + (_fee if _q > 0 else -_fee))
                if _q < 0:
                    # A sale whose commission exceeds its gross is
                    # entered as 0 (a negative total is refused below),
                    # so 0 is its correct total — comparing against the
                    # negative figure warned on exactly that (S029-01).
                    _expected = max(_expected, 0.0)
                _total = abs(tx['net_amount'])
                if (tx['price'] > 0 and abs(_q) > 0
                        # A futures line is checked only with its size
                        # on the line (`x1000`).
                        and (not _is_fut or tx.get('multiplier'))
                        and abs(_total - _expected) >
                        max(0.05, 0.01 * max(_expected, 1.0))):
                    print(
                        f"warning: {_where(source)}.tt line total "
                        f"{_total:.2f} differs from "
                        f"qty*price{f'*{_mult:g}' if _mult != 1 else ''}"
                        f"{'+' if _q > 0 else '-'}fee = "
                        f"{_expected:.2f} by more than 1%: {line.strip()!r} "
                        f"— check for a typo (the total IS what the engine "
                        f"books as cost/proceeds).",
                        file=sys.stderr,
                    )

        elif action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'):
            tx['symbol'] = parts[3].upper()
            tx['quantity'] = _tt_num(parts[4])
            tx['currency'] = parts[5].upper()
            val = _tt_num(parts[7])
            tx['gross_amount'] = val
            # Optional 9th column: the withholding-NETTED amount (the
            # emitter writes it when net != gross so a round-trip
            # doesn't inflate income to the gross figure).
            tx['net_amount'] = (_tt_num(parts[8])
                                if len(parts) > 8 else val)
            tx['type'] = action.lower()

        elif action == 'INTEREST' or action == 'FEE':
            tx['currency'] = parts[3].upper()
            tx['net_amount'] = _tt_num(parts[4])
            tx['type'] = action.lower()
            tx['symbol'] = 'CASH'

        elif action == 'ADJUST' or action == 'DISALLOW':
            tx['symbol'] = parts[3].upper()
            tx['currency'] = parts[4].upper()
            tx['net_amount'] = _tt_num(parts[5])
    except (ValueError, IndexError) as e:
        raise ValueError(
            f"{_where(source)}taxjson-convert-tt: malformed .tt line "
            f"({type(e).__name__}: "
            f"{e}). Action={action!r}, line={line!r}. Hand-edit or remove "
            f"the row before re-running — silent skip would lose a "
            f"transaction the engine downstream needs."
        ) from e

    # A SELL total is the POSITIVE net proceeds (qty x price - fee;
    # direction lives in the qty sign). A negative one was booked as
    # negative proceeds -- a +1,000 gain became a -3,000 loss with no
    # word, because the net >= 0 schema rule never runs on .tt rows
    # (audit R1-117). It is ambiguous (a cash-signed proceeds figure, or
    # a commission larger than the proceeds), so refuse it. A negative
    # BUY total cannot mean anything but the cash sign (qty x price +
    # fee is never negative) and stays read as its magnitude.
    if (action in ('BUYSELL', 'ASSIGN') and tx.get('quantity', 0) < 0
            and tx.get('net_amount', 0) < 0):
        raise ValueError(
            f"{_where(source)}{action} sell total {parts[7]} is negative "
            f"— a .tt total is the POSITIVE net proceeds (qty x price - "
            f"commission); the sell direction lives in the negative qty. "
            f"If you typed the cash sign, drop the '-'; if the commission "
            f"exceeds the proceeds, enter 0. Line: {line.strip()!r}")

    _warn_unknown_suffix(tx, line, source)
    tx['id'] = compute_tt_id(tx)
    return tx


def _warn_unknown_suffix(tx: dict, line: str, source: str) -> None:
    """A dotted symbol must end in a known market suffix — the rule the
    schema applies to every parser row, which .tt rows never met. A
    typo'd lot (`XYZ.TSX`, `XYZ.CA`) is its own ACB pool: the broker's
    sale of XYZ.TO opens a phantom short and the gain drops out
    (S028-19). Bare symbols (crypto) and option/futures symbols are
    exempt."""
    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    from taxjson.lib.core import is_option_symbol
    for key in ('symbol', 'symbol_new'):
        sym = tx.get(key) or ''
        if ('.' not in sym or sym == 'CASH' or is_option_symbol(sym)
                or sym.startswith(_FUTURES_PREFIXES)):
            continue
        ext = sym.rsplit('.', 1)[1]
        if ext not in KNOWN_SUFFIXES:
            print(f"warning: {_where(source)}symbol {sym} ends in .{ext}, "
                  f"which is not a known market suffix "
                  f"({', '.join(sorted(KNOWN_SUFFIXES))}) — a typo here is "
                  f"its own ACB pool, and the broker's rows for the real "
                  f"listing go short: {line.strip()!r}", file=sys.stderr)


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


def tx_to_tt_line(tx: dict, date_basis: str = 'settle'):
    """Emit a single `.tt` line for one transaction, in the field order
    parse_tt_line reads. Returns None for transactions whose action
    has no `.tt` representation (e.g. OPENING_BALANCE) so the caller can
    skip them. Numeric fields use %.8f for qty/price, %.5f for totals/fees.
    A row with no currency is refused: writing CAD relabelled it
    silently (R1-133).

    A .tt line has ONE date, which the reader uses as both the trade and
    the settlement date. `date_basis='settle'` (the default — Canada
    times a disposition on settlement) writes `date_settle`: writing the
    trade date moved a Dec-31 sale settling in January into the earlier
    year on re-import (R1-130). `'trade'` writes the trade date (a
    trade-basis project)."""
    action = tx.get('action', '')
    if action not in _VALID_ACTIONS:
        return None

    date = tx.get('date', '')
    if date_basis == 'settle' and tx.get('date_settle'):
        date = tx['date_settle']
    time = tx.get('time', '09:30:00')
    symbol = tx.get('symbol', '')
    qty = float(tx.get('quantity') or 0.0)
    currency = str(tx.get('currency') or '').strip().upper()
    if not currency and action != 'SPLIT':
        raise ValueError(
            f"{action} {tx.get('date', '')} {tx.get('symbol', '')}: the "
            f"row has no currency — a .tt line needs one; refusing to "
            f"write CAD over an unknown currency. Fix the input row.")
    price = float(tx.get('price') or 0.0)
    net = float(tx.get('net_amount') or 0.0)
    # Dividend/tax tt rows quote the gross (pre-withholding) amount, so
    # prefer it when present — parsers leave gross at 0 when only net is
    # known, in which case net is the right fallback.
    gross = float(tx.get('gross_amount') or 0.0) or net
    # fee + commission: parsers split the charge across both keys
    # (Questrade books `commission` only) — reading `fee` alone dropped
    # the commission on a json->tt->json cycle (R1-130).
    fee = float(tx.get('fee') or 0.0) + float(tx.get('commission') or 0.0)

    if action in ('BUYSELL', 'ASSIGN'):
        # ACTION date time symbol qty currency price total fee
        # SIGNED total/fee: abs() re-inflated sign-preserved reversal
        # rows (and fee rebates) on a json→tt→json cycle — the exact
        # corruption the parser-level sign fixes removed. Direction
        # still comes from qty. parse_tt_line refuses a negative SELL
        # total (R1-117): a commission above the gross is written 0.
        line = (f"{action} {date} {time} {symbol} {qty:.8f} {currency} "
                f"{price:.8f} {net:.5f} {fee:.5f}")
        # A declared contract size other than the equity option's 100
        # (or any size on a futures line) rides along as `x<size>`
        # (audit S026-22).
        try:
            _m = float(tx.get('multiplier') or 0.0)
        except (TypeError, ValueError):
            _m = 0.0
        from taxjson.lib.core import is_option_symbol as _is_opt
        if _m > 0 and (str(symbol).startswith(_FUTURES_PREFIXES)
                       or (_is_opt(str(symbol)) and _m != 100.0)):
            line += f" x{_m:g}"
        return line

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
    parts = strip_tt_comment(line).split()
    if not parts or parts[0] != 'ACQUIRED':
        return None
    if len(parts) != 10 or parts[8] != 'ARRIVED':
        raise ValueError(
            f"taxjson-convert-tt: malformed ACQUIRED line — expected "
            f"`ACQUIRED <true-date> <time> <sym> <qty> <cur> <price> "
            f"<total> ARRIVED <arrival-date>`, got: {line.strip()!r}")
    _, tdate, ttime, sym, qty, cur, price, total = parts[:8]
    arrival = parts[9]
    qty_val = _tt_num(qty)              # a decimal comma raises here
    qty = qty.replace(',', '')
    if qty_val <= 0:
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
    # utf-8-sig: an editor's byte-order mark used to reach the first
    # action as '\ufeffBUYSELL' ("unknown .tt action", R1-133).
    from taxjson.lib.cli_diag import read_text_utf8
    with io.StringIO(read_text_utf8(input_path)) as f:
        for lineno, line in enumerate(f, 1):
            source = f"{input_path.name}:{lineno}"
            try:
                expanded = expand_acquired(line)
            except ValueError as e:
                raise ValueError(f"{source}: {e}") from e
            for one in (expanded if expanded is not None else [line]):
                tx = parse_tt_line(one, account_name=account_name,
                                   source=source)
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
    # Byte-identical DECLARED counter-transfers in ONE file (two ACQUIRED
    # lots of the same size, price and arrival day) are two arrival legs,
    # not a duplicate: the always-on dedup kept one and the other lot's
    # arrival was never netted — a phantom 100 shares (S029-08). They are
    # exempt from the fill markers above (the declaration is matched by
    # exact description), so combine them into one row carrying the sum.
    combined = []
    by_id = {}
    for tx in transactions:
        if (tx.get('action') == 'TRANSFER'
                and tx.get('description') == MANUAL_TRANSFER_DECLARATION):
            first = by_id.get(tx['id'])
            if first is not None:
                first['quantity'] += tx['quantity']
                first['net_amount'] += tx['net_amount']
                continue
            by_id[tx['id']] = tx
        combined.append(tx)
    transactions = combined
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


def json_to_tt_lines(input_path: Path, date_basis: str = 'settle'):
    with input_path.open('r', encoding='utf-8') as f:
        data = json.load(f)
    transactions = data.get('transactions', data) if isinstance(data, dict) else data
    skipped = 0
    two_dates = 0
    mults = []
    for tx in transactions:
        line = tx_to_tt_line(tx, date_basis=date_basis)
        if line is None:
            skipped += 1
            continue
        if (tx.get('date_settle') and tx.get('date')
                and tx['date_settle'] != tx['date']):
            two_dates += 1
        m = tx.get('multiplier')
        try:
            if (m not in (None, '') and float(m) not in (1.0, 100.0)
                    and not re.search(r' x[0-9.]+$', line)):
                mults.append(f"{tx.get('symbol')} x{float(m):g}")
        except (TypeError, ValueError):
            mults.append(f"{tx.get('symbol')} x{m!r}")
        yield line
    if skipped:
        print(
            f"note: skipped {skipped} transaction(s) with no .tt representation",
            file=sys.stderr,
        )
    if two_dates:
        which = ("SETTLEMENT" if date_basis == 'settle' else "TRADE")
        print(
            f"note: {two_dates} row(s) have a trade date and a later "
            f"settlement date; a .tt line carries one date, so each was "
            f"written with its {which} date (--date-basis "
            f"{'trade' if date_basis == 'settle' else 'settle'} for the "
            f"other). The year a sale lands in follows that date.",
            file=sys.stderr,
        )
    if mults:
        print(
            f"warning: {len(mults)} row(s) carry a contract multiplier the "
            f".tt format cannot hold ({', '.join(mults[:5])}"
            f"{' ...' if len(mults) > 5 else ''}); the total is kept, but "
            f"the re-imported row loses the declared size.",
            file=sys.stderr,
        )


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` through a temp file in the same folder:
    the old contents stay until the new ones are complete."""
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


@guard_main("taxjson-convert-tt")
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
    parser.add_argument(
        "--date-basis",
        choices=("settle", "trade"),
        default=None,
        help=(
            "json->tt only: which date a .tt line (one date) carries — "
            "the settlement date or the trade date. Default: the "
            "project's tax_date when the input sits in a project's work/ "
            "(Canada: settle, US: trade); outside a project it is "
            "required when a row's trade and settle dates differ."
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
        # JSON → tt. The one date a .tt line keeps is the tax-year date
        # basis, which is the country's: never a silent Canadian settle
        # default (partition INPUTS-14 — a US book came back with its
        # Dec-31 sales in January).
        date_basis = args.date_basis
        if date_basis is None:
            from taxjson.lib.country import CountryError
            from taxjson.lib.phantom_holdings import tax_date_near
            try:
                date_basis = tax_date_near(input_path)
            except CountryError as e:
                print(f"taxjson-convert-tt: error: taxjson.toml: {e}",
                      file=sys.stderr)
                sys.exit(2)
        if date_basis is None:
            data = json.loads(input_path.read_text(encoding='utf-8'))
            rows = (data.get('transactions', data)
                    if isinstance(data, dict) else data)
            if any(isinstance(t, dict) and t.get('date_settle')
                   and t.get('date') and t['date_settle'] != t['date']
                   for t in rows or []):
                print("taxjson-convert-tt: error: rows have a trade date "
                      "and a different settlement date, and a .tt line "
                      "keeps one: pass --date-basis settle (Canada, CRA) "
                      "or --date-basis trade (US, IRS) — no taxjson.toml "
                      "beside the input to read tax_date from.",
                      file=sys.stderr)
                sys.exit(2)
            date_basis = 'settle'       # every row has one date: moot
        # Every line first, then one write: a row that fails half-way
        # used to leave a partial file — and truncate whatever the
        # output held before (R1-133).
        text = "".join(line + "\n" for line in json_to_tt_lines(
            input_path, date_basis=date_basis))
        if args.output:
            _write_atomic(Path(args.output), text)
        else:
            sys.stdout.write(text)
    else:
        # tt → JSON  (default, also handles unknown extensions)
        result = tt_to_json(input_path, args.account_name)
        if args.output:
            _write_atomic(Path(args.output),
                          json.dumps(result, indent=2, sort_keys=True))
        else:
            json.dump(result, sys.stdout, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
