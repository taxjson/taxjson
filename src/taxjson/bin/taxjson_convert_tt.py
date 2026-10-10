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
each action is in parse_tt_line / tx_to_tt_line below (and
docs/settings.md, ".tt files").
"""

from taxjson.lib.stage_msg import emit_line
import argparse
import difflib
import hashlib
import io
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from taxjson.lib.cli_diag import guard_main

_VALID_ACTIONS = (
    'BUYSELL', 'TRANSFER', 'SPLIT', 'ASSIGN', 'ADJUST', 'DISALLOW',
    'DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'OPENING',
)
# Line-level sugar expanded by tt_to_json before parse_tt_line, and
# INKIND (the value of an in-kind move between a taxable and a registered
# account: read by `taxjson run`, lib/in_kind — never a row of the books).
_SUGAR_ACTIONS = ('ACQUIRED', 'INKIND')
# Dated events written date first (lib/dated_events): a JOURNAL between
# two listings of one security inside the account, and a ticker change
# (RENAME). `taxjson run` reads them from the .tt files of every account
# and books them as events (the journal's transfer legs, the rename's
# SPLIT row in each account holding the old symbol) — they are checked
# here and never rows of the converted file.
_EVENT_ACTIONS = ('JOURNAL', 'RENAME', 'ALLOWLOSS', 'FXCONV', 'CASHMOVE',
                  'CASHOPEN', 'CASHBAL', 'CASHBOOK')
# ALLOWLOSS (lib/loss_overrides) is a filing position against the loss
# rule on one sale of the account: read by `taxjson run`, checked here,
# never a row of the books.
JOURNAL_FORM = "JOURNAL <date> <FROM> <TO> <qty> [separate]"
# The trailing word that marks a .tt JOURNAL line as a journal of its
# own, never a restatement of a broker's journal near it
# (lib/dated_events.Journal.separate).
JOURNAL_SEPARATE = "separate"
RENAME_FORM = "RENAME <date> <OLD> <NEW> [late=fold|late=separate]"
# The time an OPENING row is booked at: the start of the snapshot day,
# before anything else that day (the line itself has no time column).
OPENING_TIME = '00:00:00'

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
# Optional income facts at the END of a DIVIDEND / DIVIDEND_IN_LIEU /
# TAX / ADJUST line, as `key=value` tokens: the record date that dates
# trust income and ROC, the broker's "distribution" label, the paying
# dealer's and the issuer's countries (an s.260 payment in lieu by a
# Canadian dealer is a deemed dividend), and an ADJUST's kind (`roc`,
# `dist`). A json -> tt -> json round trip dropped them all, moving
# income to the pay year and a deemed dividend to other income with no
# word (audit A2-0291, A2-0631).
_FACT_KEYS = {'record': 'record_date', 'ex': 'ex_date',
              'label': 'income_label', 'dealer': 'dealer_country',
              'issuer': 'issuer_country', 'type': 'type'}
_FACT_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'ADJUST')
_FACT_RE = re.compile(r'^([a-z]+)=(\S+)$')
_ADJUST_TYPES = ('roc', 'dist')


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
    valid = _VALID_ACTIONS + _SUGAR_ACTIONS + _EVENT_ACTIONS
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
    if action == 'OPENING':
        if any(t.lower().startswith('cost=') for t in parts[1:]):
            # (checked by parse_unknown_opening_line; never a row)
            raise ValueError(
                f"{_where(source)}an `OPENING ... cost=unknown` line is "
                f"units held before the data, read by `taxjson run` "
                f"(lib/missing_history) — not a row of the books: "
                f"{line.strip()!r}")
        return parse_opening_line(parts, line, account_name, source)
    if len(parts) < 3:
        raise ValueError(
            f"{_where(source)}malformed .tt line — {action} needs at "
            f"least a date and a time: {line.strip()!r}")
    _check_date(parts[1], 'date', line, source)
    _check_time(parts[2], line, source)
    facts = {}
    if action in _FACT_ACTIONS:
        while parts and _FACT_RE.match(parts[-1]):
            key, val = _FACT_RE.match(parts.pop()).groups()
            facts[key] = val
        facts = _check_facts(action, facts, line, source)
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
            # hand-typed `samplg.us` was its own ACB pool and the broker's
            # sale of SAMPLG.US opened a phantom short (S001-06).
            tx['symbol'] = parts[3].upper()
            if action == 'SPLIT':
                tx['symbol_new'] = parts[4].upper()
                tx['quantity'] = _tt_num(parts[5])
                tx['currency'] = ''     # no money: a ratio (a US project has no CAD rates)
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
                # (the run reads this warning back: lib/tt_totals)
                from taxjson.lib.tt_totals import \
                    tolerance as _tt_tolerance
                _is_fut = tx['symbol'].startswith(_FUTURES_PREFIXES)
                _mult = (tx['multiplier'] if tx.get('multiplier')
                         else 100.0 if (is_option_symbol(tx['symbol'])
                                        and not _is_fut)
                         else 1.0)
                _q = tx['quantity']
                _fee = tx.get('fee', 0.0)
                _expected = (abs(_q) * tx['price'] * _mult
                             + (_fee if _q > 0 else -_fee))
                # A sale whose commission exceeds its gross nets
                # NEGATIVE proceeds (qty x price - fee < 0): that signed
                # total is what the line carries now (A2-0622/A2-0623),
                # so a SELL total is compared signed. The old `enter 0`
                # dropped the excess commission from the loss — the
                # check now says so instead of calling 0 correct.
                _total = (tx['net_amount'] if _q < 0
                          else abs(tx['net_amount']))
                if (_q < 0 and _expected < 0 and tx['price'] > 0
                        and abs(tx['net_amount']) < 0.005
                        and (not _is_fut or tx.get('multiplier'))):
                    emit_line(
                        f"warning: {_where(source)}.tt sell total 0 on a "
                        f"sale whose commission {_fee:.2f} exceeds its "
                        f"gross {abs(_q) * tx['price'] * _mult:.2f}: the "
                        f"proceeds are {_expected:.2f}, and 0 leaves "
                        f"{-_expected:.2f} of commission out of the loss "
                        f"— write the negative total ({_expected:.2f}): "
                        f"{line.strip()!r}", file=sys.stderr)
                elif (tx['price'] > 0 and abs(_q) > 0
                        # A futures line is checked only with its size
                        # on the line (`x1000`).
                        and (not _is_fut or tx.get('multiplier'))
                        and abs(_total - _expected) >
                        _tt_tolerance(_expected)):
                    emit_line(
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

    # A SELL total is the net proceeds (qty x price - fee; direction
    # lives in the qty sign). A negative one is either a cash-signed
    # proceeds figure typed with a '-' (booked as negative proceeds, a
    # +1,000 gain became a -3,000 loss with no word: audit R1-117) or a
    # sale whose commission exceeds its gross (a penny option close,
    # S017-00). Only the second is unambiguous: the line's own fee is
    # larger than qty x price (x size) and the total is qty x price -
    # fee. That one is booked signed, like every parser books it (the
    # whole commission is an outlay, CA-DISP-01); any other negative
    # SELL total is refused. The .tt writers emit exactly that signed
    # total, so json -> tt -> json round-trips (A2-0292, A2-0620,
    # A2-0621, A2-0622, A2-0623, A2-1073, A2-1226, A2-1227). A negative
    # BUY total cannot mean anything but the cash sign (qty x price +
    # fee is never negative): the row keeps it as typed, and the engines
    # take a buy's cost as its magnitude (core _trade_money).
    if (action in ('BUYSELL', 'ASSIGN') and tx.get('quantity', 0) < 0
            and tx.get('net_amount', 0) < 0
            and not _excess_commission_sale(tx)):
        raise ValueError(
            f"{_where(source)}{action} sell total {parts[7]} is negative "
            f"— a .tt total is the net proceeds (qty x price - "
            f"commission); the sell direction lives in the negative qty. "
            f"If you typed the cash sign, drop the '-'. A negative total "
            f"is read only when the line's commission exceeds qty x "
            f"price{' (add the contract size, e.g. x1000)' if str(tx.get('symbol') or '').startswith(_FUTURES_PREFIXES) and not tx.get('multiplier') else ''} "
            f"and the total equals qty x price - commission. "
            f"Line: {line.strip()!r}")

    # Not part of the id (as in TaxTransaction.compute_id).
    tx.update(facts)
    _canonical_ca_symbols(tx)
    _warn_unknown_suffix(tx, line, source)
    tx['id'] = compute_tt_id(tx)
    return tx


def _is_date(tok: str) -> bool:
    if not _DATE_RE.match(tok or ''):
        return False
    try:
        datetime.strptime(tok, '%Y-%m-%d')
    except ValueError:
        return False
    return True


def parse_journal_line(line: str, source: str = ''):
    """`JOURNAL <date> <FROM> <TO> <qty> [separate]` -> {date, from, to,
    quantity, separate}: a move of `qty` units from listing FROM to
    listing TO of one security inside this account (a Norbert's gambit's
    journal, a TSX line moved to its NYSE line), or None when the line is
    not a JOURNAL line. No time column. A trailing `separate` says the
    line is a journal of its own, never the restatement of a broker's
    journal near it: booked in full, nothing asked (v0.24.1 leftovers,
    3). `taxjson run` books it as the move's two transfer legs and
    joins the listings (lib/dated_events, tax-logic CA-XLIST-04 /
    US-XLIST-03): no disposition. Raises ValueError naming the form on a
    malformed line."""
    where = _where(source)
    body = strip_tt_comment(line)
    parts = body.split()
    if not parts or parts[0] != 'JOURNAL':
        return None
    shown = body.strip()
    separate = (len(parts) == 6
                and parts[5].lower() == JOURNAL_SEPARATE)
    if separate:
        parts = parts[:5]
    if len(parts) != 5:
        hint = ''
        if len(parts) == 3 and not _is_date(parts[1]):
            hint = (" — `JOURNAL FROM TO` is the old ticker.map line; in a "
                    ".tt file a journal is dated and sized")
        elif len(parts) == 6:
            hint = (f" — the only word after the quantity is "
                    f"`{JOURNAL_SEPARATE}` (a journal of its own)")
        raise ValueError(
            f"{where}malformed JOURNAL line — expected `{JOURNAL_FORM}` "
            f"(a move of <qty> units from listing FROM to listing TO in "
            f"this account){hint}: {shown!r}")
    _, date, frm, to, qty = parts
    if not _is_date(date):
        raise ValueError(
            f"{where}malformed JOURNAL line — the date {date!r} is not "
            f"YYYY-MM-DD (expected `{JOURNAL_FORM}`): {shown!r}")
    # Spelled as the account's other .tt rows are (no currency: only the
    # unambiguous Canadian venues fold, as on a SPLIT line).
    _sy = {'action': 'SPLIT', 'symbol': frm.upper(), 'symbol_new': to.upper()}
    _canonical_ca_symbols(_sy)
    frm, to = _sy['symbol'], _sy['symbol_new']
    if frm == to:
        raise ValueError(
            f"{where}malformed JOURNAL line — FROM and TO are the same "
            f"listing {frm} (expected `{JOURNAL_FORM}`): {shown!r}")
    for sym in (frm, to):
        kind = _derivative_kind(sym)
        if kind:
            # A contract is never units of a share listing: journaling it
            # would merge it into the stock's pool (pre-release review H7).
            raise ValueError(
                f"{where}JOURNAL line names {kind} {sym}: a journal moves "
                f"units between two LISTINGS of one security (shares, "
                f"units); an option or a future is never journaled — book "
                f"its exercise, assignment or sale as the broker's rows "
                f"show it: {shown!r}")
    try:
        q = _tt_num(qty)
    except ValueError as e:
        raise ValueError(
            f"{where}malformed JOURNAL line — the quantity {qty!r} is not "
            f"a number ({e}; expected `{JOURNAL_FORM}`): {shown!r}") from None
    if not q > 0:
        raise ValueError(
            f"{where}malformed JOURNAL line — the quantity must be "
            f"positive (the units moved from {frm} to {to}; expected "
            f"`{JOURNAL_FORM}`): {shown!r}")
    if q > _MAX_EVENT_QTY:
        raise ValueError(
            f"{where}JOURNAL line — the quantity {qty} is not a plausible "
            f"number of units (more than {_MAX_EVENT_QTY:,.0f}): "
            f"{shown!r}")
    _not_in_future(date, 'JOURNAL', where, shown)
    return {'date': date, 'from': frm, 'to': to, 'quantity': q,
            'line': shown, 'separate': separate}


# The most units a dated event may move (a JOURNAL line): no position
# holds a trillion units; a bigger number is a typo (1e308).
_MAX_EVENT_QTY = 1e12

# Units held before the data starts, cost unknown (lib/missing_history:
# missing history, read by `taxjson run`).
UNKNOWN_OPENING_FORM = ('OPENING <date> <SYMBOL> <qty> cost=unknown '
                        '[reason="..."]')


def _cut_comment_outside_quotes(line: str) -> str:
    """`line` up to its first `#` outside double quotes (a reason="..."
    may hold a `#`)."""
    inq = False
    for i, ch in enumerate(line):
        if ch == '"':
            inq = not inq
        elif ch == '#' and not inq:
            return line[:i]
    return line


def parse_unknown_opening_line(line: str, source: str = ''):
    """`OPENING <date> <SYMBOL> <qty> cost=unknown [reason="..."]` ->
    {date, symbol, quantity, reason, line}: <qty> units of SYMBOL held
    on <date>, bought before the data starts at a cost the files do not
    give — missing history (tax-logic
    CA-ACB-11 / US-BASIS-04). None when the line is not an OPENING line
    with a `cost=` word (a positions-report OPENING line has a currency
    and a total instead). `taxjson run` reads it from the account's .tt
    files (lib/missing_history.read_tt_openings); it is never a row of
    the converted file. Raises ValueError naming the form on a
    malformed line."""
    import shlex
    where = _where(source)
    body = _cut_comment_outside_quotes(line)
    head = body.split()
    if not head or head[0] != 'OPENING' or not any(
            t.lower().startswith('cost=') for t in head[1:]):
        return None
    shown = body.strip()
    try:
        parts = shlex.split(body, posix=True)
    except ValueError:
        raise ValueError(
            f"{where}malformed OPENING line — an unclosed quote (expected "
            f"`{UNKNOWN_OPENING_FORM}`): {shown!r}") from None
    reason = None
    rest = []
    for t in parts:
        if t.lower().startswith('reason='):
            if reason is not None:
                raise ValueError(
                    f"{where}malformed OPENING line — two reason= words "
                    f"(expected `{UNKNOWN_OPENING_FORM}`): {shown!r}")
            reason = t.split('=', 1)[1]
            continue
        rest.append(t)
    reason = (reason or '').strip()
    if len(rest) != 5 or rest[4].lower() != 'cost=unknown':
        bad = [t for t in rest if t.lower().startswith('cost=')]
        hint = (f" — the cost of units held before the data is `unknown` "
                f"(got {bad[0]!r}; units whose cost you know are a "
                f"positions-report line `OPENING <date> <symbol> <qty> "
                f"<currency> <total-cost>`)"
                if bad and bad[0].lower() != 'cost=unknown' else '')
        raise ValueError(
            f"{where}malformed OPENING line — expected "
            f"`{UNKNOWN_OPENING_FORM}`{hint}: {shown!r}")
    _, date, sym, qty, _cost = rest
    if not _is_date(date):
        raise ValueError(
            f"{where}malformed OPENING line — the date {date!r} is not "
            f"YYYY-MM-DD (expected `{UNKNOWN_OPENING_FORM}`): {shown!r}")
    _sy = {'action': 'SPLIT', 'symbol': sym.upper()}
    _canonical_ca_symbols(_sy)
    sym = _sy['symbol']
    if sym.startswith(_FUTURES_PREFIXES):
        raise ValueError(
            f"{where}OPENING cost=unknown on a futures contract ({sym}) is "
            f"not supported: futures are booked on their own basis "
            f"(lib/futures.py): {shown!r}")
    try:
        q = _tt_num(qty)
    except ValueError as e:
        raise ValueError(
            f"{where}malformed OPENING line — the quantity {qty!r} is not "
            f"a number ({e}; expected `{UNKNOWN_OPENING_FORM}`): "
            f"{shown!r}") from None
    if not q > 0 or q > _MAX_EVENT_QTY:
        raise ValueError(
            f"{where}malformed OPENING line — the quantity must be a "
            f"positive number of units held before the data (expected "
            f"`{UNKNOWN_OPENING_FORM}`): {shown!r}")
    _not_in_future(date, 'OPENING', where, shown)
    return {'date': date, 'symbol': sym, 'quantity': q, 'reason': reason,
            'line': shown}


def unknown_opening_text(date: str, symbol: str, quantity: float,
                         reason: str = '') -> str:
    """The .tt line parse_unknown_opening_line reads back."""
    r = ' '.join(str(reason or '').replace('"', "'").split())
    q = _num(quantity)
    if '.' in q:
        q = q.rstrip('0').rstrip('.')
    return (f"OPENING {date} {symbol} {q} cost=unknown"
            + (f' reason="{r}"' if r else ''))


def _not_in_future(date: str, kind: str, where: str, shown: str) -> None:
    """A dated event is something that happened: a date after today is
    refused (a typo of the year, or a line written ahead of the event)."""
    from datetime import date as _date
    if date > _date.today().isoformat():
        raise ValueError(
            f"{where}{kind} line is dated {date}, in the future — a dated "
            f"event is written once it has happened (check the year): "
            f"{shown!r}")


def _derivative_kind(sym: str) -> str:
    """"an option contract" / "a future" for such a symbol, else ""."""
    from taxjson.lib.core import is_option_symbol
    if is_option_symbol(sym):
        return 'an option contract'
    if str(sym or '').startswith(_FUTURES_PREFIXES):
        return 'a future'
    return ''


def _rename_derivative_check(old: str, new: str, date: str, where: str,
                             shown: str) -> None:
    """A RENAME between a share listing and a contract is refused (a
    contract never becomes shares by a ticker change: pre-release review
    H7), and so is one between two contracts: an option follows its
    underlying's ticker change (lib/renames), so the line to write is the
    stock's; a series with another expiry, strike or right is another
    contract, not a new name."""
    from taxjson.lib.core import parse_option_underlying
    ko, kn = _derivative_kind(old), _derivative_kind(new)
    if not ko and not kn:
        return
    if not ko or not kn:
        sym, kind = (old, ko) if ko else (new, kn)
        raise ValueError(
            f"{where}RENAME line renames {kind} ({sym}) "
            f"{'into' if ko else 'from'} a share listing: a ticker change "
            f"keeps the security, and a contract never becomes shares (or "
            f"shares a contract) by one — book the exercise, assignment or "
            f"sale as the broker's rows show it: {shown!r}")
    uo, un = parse_option_underlying(old), parse_option_underlying(new)
    if uo and un and old[len(uo.rsplit('.', 1)[0]):] == \
            new[len(un.rsplit('.', 1)[0]):] and uo != un:
        raise ValueError(
            f"{where}RENAME line renames an option contract: an option "
            f"follows its underlying's ticker change — declare the stock's "
            f"instead, `RENAME {date} {uo} {un}`: {shown!r}")
    raise ValueError(
        f"{where}RENAME line renames {ko} ({old}) into {kn} ({new}): another "
        f"expiry, strike or right is another contract, not a new name of "
        f"the same one — book the broker's rows as they are (an option "
        f"follows its underlying's ticker change: declare the stock's "
        f"RENAME): {shown!r}")


def _option_contract(sym: str):
    """(futures option?, market suffix or '', expiry, right, strike) of
    an OCC option symbol: what makes it one contract whatever the root
    is spelled (a class letter, an adjusted-series digit, a broker's own
    spelling of the underlying), or None for another symbol."""
    from taxjson.lib.core import (_OCC_OPTION_RE, parse_option_expiry,
                                  parse_option_right, parse_option_strike)
    m = _OCC_OPTION_RE.match(sym or '')
    if not m:
        return None
    return (m.group(1).startswith(_FUTURES_PREFIXES), m.group(3) or '',
            parse_option_expiry(sym), parse_option_right(sym),
            parse_option_strike(sym))


def _join_derivative_check(keyword: str, frm: str, to: str, where: str,
                           shown: str) -> None:
    """An UNDATED ticker.map line (GLOBAL, TOBASE, a legacy JOURNAL, a
    RENAME without a date) makes FROM the same security as TO at every
    date. Refused (ValueError naming the line): an option contract or a
    future joined with a share listing (a contract is never the shares),
    an option joined with a future, and two option contracts that differ
    in expiry, right, strike or market (another contract; a US option and
    a Montreal option on one stock are different property). Allowed: a
    respelling of ONE contract — the same expiry, right, strike and
    market (a missing suffix matches any), only the root spelled
    otherwise (a class letter, an adjusted-series digit: `GLOBAL
    QZB.B250620C00010000.TO QZB250620C00010000.TO`) — and two futures
    (their spellings are not checked). (Second pre-release review, 6.)"""
    ko, kn = _derivative_kind(frm), _derivative_kind(to)
    if not ko and not kn:
        return
    if not ko or not kn:
        sym, kind, other = (frm, ko, to) if ko else (to, kn, frm)
        raise ValueError(
            f"{where}{keyword} joins {kind} ({sym}) with a share listing "
            f"({other}) at every date: a contract is never the same "
            f"security as shares — book an exercise, assignment or sale "
            f"as the broker's rows show it, and join the shares' symbols "
            f"(an option follows its underlying's line): {shown!r}")
    co, cn = _option_contract(frm), _option_contract(to)
    if co is None and cn is None:
        return                                  # two futures
    if co is None or cn is None or co[0] != cn[0]:
        raise ValueError(
            f"{where}{keyword} joins {ko} ({frm}) with {kn} ({to}): an "
            f"option and a future (or an option on a future and one on "
            f"shares) are different contracts: {shown!r}")
    if co[2:] != cn[2:] or (co[1] and cn[1] and co[1] != cn[1]):
        raise ValueError(
            f"{where}{keyword} joins two different option contracts "
            f"({frm}, {to}): another expiry, right, strike or market is "
            f"another contract — only a respelling of one contract (the "
            f"same expiry, right, strike and market) may be joined: "
            f"{shown!r}")


def parse_rename_line(line: str, source: str = ''):
    """`RENAME <date> <OLD> <NEW> [late=fold|late=separate]` -> {date,
    old, new, late}: a ticker change on that date (lib/renames: the
    position, cost and acquisition dates carry from OLD to NEW), or None
    when the line is not a RENAME line. `taxjson run` books it in every
    account whose books carry OLD (lib/dated_events). Two spellings of
    one listing in the books (A.TO / A.CN: a Canadian venue folds into
    .TO) return the line with `noop`, the Info line to say: nothing is
    booked (a SPLIT would strand the pool). Raises ValueError
    naming the form on a malformed line — the ticker.map form `RENAME
    OLD NEW YYYY-MM-DD` included (a .tt line is date first)."""
    from taxjson.lib.renames import parse_rename_tail
    where = _where(source)
    body = strip_tt_comment(line)
    parts = body.split()
    if not parts or parts[0] != 'RENAME':
        return None
    shown = body.strip()
    if len(parts) >= 4 and not _is_date(parts[1]) and _is_date(parts[3]):
        raise ValueError(
            f"{where}malformed RENAME line — a .tt line is date first: "
            f"`RENAME {parts[3]} {parts[1].upper()} {parts[2].upper()}"
            f"{' ' + ' '.join(parts[4:]) if parts[4:] else ''}` (expected "
            f"`{RENAME_FORM}`): {shown!r}")
    if len(parts) not in (4, 5):
        raise ValueError(
            f"{where}malformed RENAME line — expected `{RENAME_FORM}`: "
            f"{shown!r}")
    try:
        date, late = parse_rename_tail([parts[1]] + parts[4:])
    except ValueError as e:
        raise ValueError(
            f"{where}malformed RENAME line — {e} (expected "
            f"`{RENAME_FORM}`): {shown!r}") from None
    _sy = {'action': 'SPLIT', 'symbol': parts[2].upper(),
           'symbol_new': parts[3].upper()}
    _canonical_ca_symbols(_sy)
    old, new = _sy['symbol'], _sy['symbol_new']
    if old == new and parts[2].upper() != parts[3].upper():
        # Two spellings of ONE listing in the books (a Canadian venue
        # suffix folds into .TO: A.CN is A.TO). A SPLIT between them
        # would strand the pool under the old spelling: nothing to book
        # (`noop`: the Info line the run says).
        _not_in_future(date, 'RENAME', where, shown)
        return {'date': date, 'old': old, 'new': new, 'late': late,
                'line': shown,
                'noop': (f"{where}RENAME {date} {parts[2].upper()} "
                         f"{parts[3].upper()}: both are {old} in the books "
                         f"(one listing), so there is nothing to rename — "
                         f"the line is not booked: {shown!r}")}
    if old == new:
        raise ValueError(
            f"{where}malformed RENAME line — OLD and NEW are the same "
            f"symbol {old} (expected `{RENAME_FORM}`): {shown!r}")
    _rename_derivative_check(old, new, date, where, shown)
    _not_in_future(date, 'RENAME', where, shown)
    return {'date': date, 'old': old, 'new': new, 'late': late,
            'line': shown}


def parse_inkind_line(line: str, source: str = ''):
    """`INKIND <date> <symbol> <qty> <currency> <price> [<total>]
    [plan=<kind>]` -> the value of one in-kind move between this taxable
    account and a registered plan (lib/in_kind, tax-logic CA-INKIND-06 /
    US-INKIND-03), or None when the line is not an INKIND line. No time
    column. `qty` is signed as the shares move in THIS account: negative
    = out, into a plan (a contribution); positive = in, from a plan (a
    withdrawal). `price` is the fair market value per share (option: per
    share of the contract) in `currency`; give 0 and a `total` to state
    the whole value instead. `plan=` names the plan when its account is
    not in the project (rrsp, tfsa, ira ...), or picks the plan's leg. The
    line values and declares the move of this account's transfer row of
    that quantity nearest the date (within 10 days) — the run pairs it
    with the plan's transfer row, or takes it as a move to a plan outside
    the project. `INKIND <date> <symbol> <qty> plan=own` (no value)
    declares the row a move of your own, never in kind; it returns
    plan "own" and total None. Raises ValueError on a malformed line."""
    where = _where(source)
    body = strip_tt_comment(line)
    parts = body.split()
    if not parts or parts[0] != 'INKIND':
        return None
    facts = {}
    while parts and (_FACT_RE.match(parts[-1])
                     or re.match(r'^[a-z]+=$', parts[-1])):
        k, _eq, v = parts.pop().partition('=')
        facts[k] = v
    form = ("`INKIND <date> <symbol> <qty> <currency> <price> [<total>] "
            "[plan=<kind>]` (no time column; qty negative = out of this "
            "account into a plan, positive = in from a plan), or "
            "`INKIND <date> <symbol> <qty> plan=own` for a move of your own")
    if 'plan' in facts and not facts['plan']:
        raise ValueError(f"{where}INKIND plan= needs a value (the plan's "
                         f"kind: rrsp, tfsa, ira ...; or own for a move "
                         f"of your own): {line.strip()!r}")
    bad = sorted(set(facts) - {'plan'})
    if bad:
        raise ValueError(f"{where}INKIND line: unknown key {bad[0]}= (only "
                         f"plan=<kind>): {line.strip()!r}")
    if len(parts) > 2 and _TIME_RE.match(parts[2]):
        raise ValueError(f"{where}an INKIND line has no time column: "
                         f"{form}: {line.strip()!r}")
    if (facts.get('plan') or '').lower() == 'own':
        if len(parts) not in (4, 6, 7):
            raise ValueError(f"{where}malformed INKIND plan=own line — "
                             f"expected `INKIND <date> <symbol> <qty> "
                             f"plan=own` (no value): {line.strip()!r}")
        _, day, sym, qty_t = parts[:4]
        _check_date(day, 'date', line, source)
        try:
            qty = _tt_num(qty_t)
        except ValueError as e:
            raise ValueError(f"{where}malformed INKIND line ({e}): "
                             f"{line.strip()!r}") from e
        if abs(qty) < 1e-12:
            raise ValueError(f"{where}INKIND quantity is 0 — the transfer "
                             f"row's quantity, negative for shares out: "
                             f"{line.strip()!r}")
        return {'date': day, 'symbol': sym.upper(), 'quantity': qty,
                'currency': '', 'total': None, 'plan': 'own',
                'source': source}
    if len(parts) not in (6, 7):
        raise ValueError(f"{where}malformed INKIND line — expected {form}, "
                         f"got {len(parts) - 1} field(s): {line.strip()!r}")
    _, day, sym, qty_t, cur, price_t = parts[:6]
    _check_date(day, 'date', line, source)
    try:
        qty = _tt_num(qty_t)
        price = _tt_num(price_t)
        total = _tt_num(parts[6]) if len(parts) == 7 else None
    except ValueError as e:
        raise ValueError(f"{where}malformed INKIND line ({e}): "
                         f"{line.strip()!r}") from e
    if abs(qty) < 1e-12:
        raise ValueError(f"{where}INKIND quantity is 0 — negative for "
                         f"shares out to a plan, positive for shares in "
                         f"from one: {line.strip()!r}")
    if price < 0 or (total is not None and total < 0):
        raise ValueError(f"{where}INKIND value is negative — the fair "
                         f"market value is a positive amount: "
                         f"{line.strip()!r}")
    if not re.match(r'^[A-Z]{3}$', cur.upper()):
        raise ValueError(f"{where}INKIND currency {cur!r} is not a "
                         f"three-letter code (CAD, USD): {line.strip()!r}")
    from taxjson.lib.core import is_option_symbol
    size = 100.0 if is_option_symbol(sym.upper()) else 1.0
    by_price = abs(qty) * price * size
    if total is None:
        if price <= 0:
            raise ValueError(f"{where}INKIND line has no value — give the "
                             f"fair market value per share, or 0 and the "
                             f"total: {line.strip()!r}")
        total = by_price
    elif price > 0 and abs(total - by_price) > max(
            0.05, 0.01 * max(by_price, 1.0)):
        raise ValueError(f"{where}INKIND total {total:.2f} differs from "
                         f"qty x price {by_price:.2f} — give one of them "
                         f"(price 0 and the total, or the price alone): "
                         f"{line.strip()!r}")
    if not math.isfinite(total):
        raise ValueError(f"{where}INKIND value is not a finite amount "
                         f"(qty x price overflows) — give the fair market "
                         f"value per share: {line.strip()!r}")
    if total <= 0:
        raise ValueError(f"{where}INKIND value is 0 — a move in kind is "
                         f"valued at the shares' fair market value: "
                         f"{line.strip()!r}")
    plan = (facts.get('plan') or '').lower()
    if plan:
        from taxjson.lib.country import PLAN_COUNTRY
        if plan not in PLAN_COUNTRY or plan == 'taxable':
            raise ValueError(f"{where}INKIND plan={plan!r} is not a "
                             f"registered plan kind: {line.strip()!r}")
    return {'date': day, 'symbol': sym.upper(), 'quantity': qty,
            'currency': cur.upper(), 'total': float(total), 'plan': plan,
            'source': source}


def parse_opening_line(parts, line: str, account_name: str,
                       source: str) -> dict:
    """`OPENING <snapshot-date> <symbol> <qty> <currency> <total-cost>
    [<lot-date>]` -> an opening-balance row: a BUYSELL of type
    `opening` (core.OPENING_TYPE) dated the snapshot day at 00:00:00,
    with the lot's acquisition date in `lot_date`. It sets a position
    and its cost but is not a purchase (tax-logic CA-OPEN-01 /
    US-OPEN-01); `taxjson opening` writes these lines from a positions
    report. Only a long position can be opened (a written option's or a
    short sale's premium depends on its write date: enter the write as
    a BUYSELL line)."""
    from taxjson.lib.core import OPENING_TYPE, is_option_symbol
    where = _where(source)
    if len(parts) > 2 and _TIME_RE.match(parts[2]):
        raise ValueError(
            f"{where}an OPENING line has no time column (it is booked "
            f"at the start of the snapshot day): `OPENING "
            f"<snapshot-date> <symbol> <qty> <currency> <total-cost> "
            f"[<lot-date>]`: {line.strip()!r}")
    if len(parts) not in (6, 7):
        raise ValueError(
            f"{where}malformed OPENING line — expected `OPENING "
            f"<snapshot-date> <symbol> <qty> <currency> <total-cost> "
            f"[<lot-date>]` (no time column), got {len(parts) - 1} "
            f"field(s): {line.strip()!r}")
    _, day, sym, qty_t, cur, total_t = parts[:6]
    lot = parts[6] if len(parts) == 7 else ''
    _check_date(day, 'snapshot date', line, source)
    if lot:
        _check_date(lot, 'lot date', line, source)
        if lot > day:
            raise ValueError(
                f"{where}OPENING lot date {lot} is after the snapshot "
                f"date {day} — a lot held on the snapshot day was "
                f"acquired on or before it: {line.strip()!r}")
    try:
        qty = _tt_num(qty_t)
        total = _tt_num(total_t)
    except ValueError as e:
        raise ValueError(f"{where}malformed OPENING line ({e}): "
                         f"{line.strip()!r}") from e
    sym = sym.upper()
    if qty <= 0:
        raise ValueError(
            f"{where}OPENING quantity must be positive (a long position "
            f"held on the snapshot day). A short position or a written "
            f"option cannot be opened from a snapshot — its premium "
            f"depends on the write: enter the write as a BUYSELL line "
            f"dated the day it was written: {line.strip()!r}")
    if total < 0:
        raise ValueError(
            f"{where}OPENING total cost {total_t} is negative — it is the "
            f"position's book cost (a positive amount): {line.strip()!r}")
    if sym.startswith(_FUTURES_PREFIXES):
        raise ValueError(
            f"{where}OPENING on a futures contract ({sym}) is not "
            f"supported: futures are booked on their own basis "
            f"(lib/futures.py). Enter the opening trade as a BUYSELL "
            f"line: {line.strip()!r}")
    if not re.match(r'^[A-Z]{3}$', cur.upper()):
        raise ValueError(
            f"{where}OPENING currency {cur!r} is not a three-letter code "
            f"(CAD, USD): {line.strip()!r}")
    size = 100.0 if is_option_symbol(sym) else 1.0
    tx = {
        'action': 'BUYSELL',
        'type': OPENING_TYPE,
        'date': day,
        'time': OPENING_TIME,
        'date_settle': day,
        'account': account_name,
        'symbol': sym,
        'quantity': qty,
        'currency': cur.upper(),
        'price': total / qty / size,
        'net_amount': total,
        'fee': 0.0,
        # The lot date is part of the description, so two lots of one
        # snapshot that differ only by it keep two ids (dedup).
        'description': ('opening balance'
                        + (f", acquired {lot}" if lot else '')),
    }
    if lot:
        tx['lot_date'] = lot
    _canonical_ca_symbols(tx)
    _warn_unknown_suffix(tx, line, source)
    tx['id'] = compute_tt_id(tx)
    return tx


def _canonical_ca_symbols(tx: dict) -> None:
    """Spell a Canadian listing as every broker parser does: ROOT.TO
    with a dotted preferred series (base.canonical_ca_listing). A .tt
    `ABC.V`, `ABC.VN` or `FTN.PRA.TO` used to stay its own ACB pool, so
    a loss sold here and the broker's repurchase of ABC.TO were never
    linked as identical property (audit A2-0300, A2-0635). A `.V` is
    Venture only on a CAD line (a SPLIT line has no currency: only the
    unambiguous .VN/.CN/.NE and undotted preferreds are folded)."""
    from taxjson.lib.brokerages.base import canonical_ca_listing
    from taxjson.lib.core import is_option_symbol
    cur = '' if tx.get('action') == 'SPLIT' else tx.get('currency', '')
    for key in ('symbol', 'symbol_new'):
        sym = tx.get(key) or ''
        if (not sym or sym == 'CASH' or is_option_symbol(sym)
                or sym.startswith(_FUTURES_PREFIXES)):
            continue
        canon = canonical_ca_listing(sym, cur)
        if canon:
            tx[key] = canon


def _check_facts(action: str, facts: dict, line: str, source: str) -> dict:
    """Validate the `key=value` income-fact tokens of one line and map
    them to their row fields."""
    out = {}
    for key, val in facts.items():
        field = _FACT_KEYS.get(key)
        if field is None:
            raise ValueError(
                f"{_where(source)}unknown .tt token {key}={val} — the "
                f"income facts are {', '.join(f'{k}=' for k in _FACT_KEYS)}"
                f": {line.strip()!r}")
        if key in ('record', 'ex'):
            _check_date(val, f"{key}=", line, source)
        elif key in ('dealer', 'issuer'):
            if not re.match(r'^[A-Z]{2}$', val):
                raise ValueError(
                    f"{_where(source)}{key}={val} must be a two-letter "
                    f"country code (CA, US): {line.strip()!r}")
        elif key == 'type':
            if action != 'ADJUST' or val not in _ADJUST_TYPES:
                raise ValueError(
                    f"{_where(source)}type={val}: only an ADJUST line "
                    f"takes a type ({', '.join(_ADJUST_TYPES)}): "
                    f"{line.strip()!r}")
        elif key == 'label':
            if not re.match(r'^[a-z_]+$', val):
                raise ValueError(
                    f"{_where(source)}label={val} must be a lower-case "
                    f"word (distribution): {line.strip()!r}")
        out[field] = val
    return out


def _fact_tokens(tx: dict, action: str) -> str:
    """The `key=value` tokens that carry a row's income facts."""
    toks = []
    for key, field in _FACT_KEYS.items():
        val = str(tx.get(field) or '').strip()
        if not val:
            continue
        if key == 'type':
            if action != 'ADJUST' or val.lower() not in _ADJUST_TYPES:
                continue
            val = val.lower()
        if ' ' in val:
            continue
        toks.append(f"{key}={val}")
    return (" " + " ".join(toks)) if toks else ""


def _num(x: float) -> str:
    """A quantity / price at full precision: `%.8f` when that is exact,
    else the shortest exact decimal. Truncating to 8 decimals changed a
    10-decimal crypto quantity on json -> tt -> json, and with it the
    row's id (audit A2-1072)."""
    x = float(x)
    s = f"{x:.8f}"
    if float(s) == x:
        return s
    from decimal import Decimal
    return format(Decimal(repr(x)), 'f')


def _excess_commission_sale(tx: dict) -> bool:
    """A SELL (or ASSIGN) line whose negative total is a commission
    larger than its gross: fee > |qty| x price x size and the total is
    |qty| x price x size - fee within the typo-check tolerance. A
    futures line needs its size on the line (`x1000`) to tell."""
    from taxjson.lib.core import is_option_symbol
    sym = str(tx.get('symbol') or '')
    is_fut = sym.startswith(_FUTURES_PREFIXES)
    if is_fut and not tx.get('multiplier'):
        return False
    mult = (tx['multiplier'] if tx.get('multiplier')
            else 100.0 if is_option_symbol(sym) else 1.0)
    gross = abs(float(tx.get('quantity') or 0.0)) * float(
        tx.get('price') or 0.0) * mult
    fee = float(tx.get('fee') or 0.0)
    expected = gross - fee
    net = float(tx.get('net_amount') or 0.0)
    return (fee > gross and expected < 0
            and abs(net - expected) <= max(0.05, 0.01 * abs(expected)))


def _warn_unknown_suffix(tx: dict, line: str, source: str) -> None:
    """A dotted symbol must end in a known market suffix — the rule the
    schema applies to every parser row, which .tt rows never met. A
    typo'd lot (`SAMPLE.TSX`, `SAMPLE.CA`) is its own ACB pool: the broker's
    sale of SAMPLE.TO opens a phantom short and the gain drops out
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
            emit_line(f"warning: {_where(source)}symbol {sym} ends in .{ext}, "
                      f"which is not a known market suffix "
                      f"({', '.join(sorted(KNOWN_SUFFIXES))}) — a typo here is "
                      f"its own cost-basis pool, and the broker's rows for the real "
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
    from taxjson.lib.core import is_opening_row
    if is_opening_row(tx):
        # OPENING <snapshot-date> <symbol> <qty> <currency> <total>
        # [<lot-date>] — the snapshot date is the row's own date.
        cur = str(tx.get('currency') or '').strip().upper()
        if not cur:
            raise ValueError(
                f"OPENING {tx.get('date', '')} {tx.get('symbol', '')}: "
                f"the row has no currency — fix the input row.")
        lot = str(tx.get('lot_date') or '').strip()
        return (f"OPENING {tx.get('date', '')} {tx.get('symbol', '')} "
                f"{_num(float(tx.get('quantity') or 0.0))} {cur} "
                f"{float(tx.get('net_amount') or 0.0):.5f}"
                + (f" {lot}" if lot else ''))

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
        # still comes from qty. A sale whose commission exceeds its
        # gross is written with its NEGATIVE total, which parse_tt_line
        # reads back when the line's fee explains it (A2-0620).
        line = (f"{action} {date} {time} {symbol} {_num(qty)} {currency} "
                f"{_num(price)} {net:.5f} {fee:.5f}")
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
        line = (f"TRANSFER {date} {time} {symbol} {_num(qty)} {currency} "
                f"{_num(price)} {net:.5f}")
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
        return f"SPLIT {date} {time} {symbol} {symbol_new} {_num(qty)}"

    if action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'):
        # ACTION date time symbol qty currency price total
        # SIGNED total: a broker reversal/refund row is negative and must
        # round-trip — abs() here re-inflated income/tax on json→tt→json
        # (the exact bug the sign-preserving IB parser fix removed), and the
        # changed net_amount hashed to a different id, breaking dedup
        # against the originals. The .tt parser reads the value signed.
        line = (f"{action} {date} {time} {symbol} {_num(qty)} {currency} "
                f"{_num(price)} {gross:.5f}")
        # Optional 9th column: the withholding-NETTED amount, emitted
        # only when it differs from gross — without it a json→tt→json
        # cycle re-parsed net as gross and inflated income. Legacy
        # positional consumers ignore trailing columns.
        if abs(net - gross) > 0.005:
            line += f" {net:.5f}"
        return line + _fact_tokens(tx, action)

    if action in ('INTEREST', 'FEE'):
        # ACTION date time currency amount  (sign preserved on interest)
        return f"{action} {date} {time} {currency} {net:.5f}"

    if action in ('ADJUST', 'DISALLOW'):
        # ACTION date time symbol currency amount
        return (f"{action} {date} {time} {symbol} {currency} {net:.5f}"
                + _fact_tokens(tx, action))

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


def _equity_account(input_path: Path, account_name: str) -> bool:
    """True when `input_path` sits in a project (inputs/<acct>/x.tt) whose
    taxjson.toml declares `account_name` without `crypto = true`. False
    outside a project or for a crypto account (unknown: no warning)."""
    try:
        from taxjson.lib.tomlcompat import tomllib
    except ImportError:                                  # pragma: no cover
        return False
    p = Path(input_path).resolve()
    for d in list(p.parents)[:3]:
        cfg = d / 'taxjson.toml'
        if cfg.is_file():
            try:
                doc = tomllib.loads(cfg.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                return False
            accts = doc.get('accounts')
            a = accts.get(account_name) if isinstance(accts, dict) else None
            return isinstance(a, dict) and a.get('crypto') is not True
    return False


def _warn_bare_equity_symbol(tx: dict, line: str, source: str) -> None:
    """A bare symbol on a .tt line of an equity (non-crypto) account
    (SAMPLH for SAMPLH.US) is its own ACB pool: the broker's sale of SAMPLH.US
    went short and its gain dropped out with nothing on the console
    (A2-0777). The unknown-suffix check exempts bare symbols because
    crypto symbols are bare; in an equity account they are not."""
    from taxjson.lib.core import is_option_symbol
    for key in ('symbol', 'symbol_new'):
        sym = tx.get(key) or ''
        if (not sym or '.' in sym or sym == 'CASH' or is_option_symbol(sym)
                or sym.startswith(_FUTURES_PREFIXES)):
            continue
        emit_line(f"warning: {_where(source)}symbol {sym} has no market suffix "
                  f"(e.g. {sym}.US, {sym}.TO) in an account that is not "
                  f"crypto = true — it is its own ACB pool, and the broker's "
                  f"rows for the real listing go short: {line.strip()!r}",
                  file=sys.stderr)


def tt_to_json(input_path: Path, account_name: str) -> dict:
    from taxjson.lib.brokerages.base import shown_name, source_key
    transactions = []
    equity = _equity_account(input_path, account_name)
    # utf-8-sig: an editor's byte-order mark used to reach the first
    # action as '\ufeffBUYSELL' ("unknown .tt action", R1-133).
    from taxjson.lib.cli_diag import read_text_utf8
    _text = read_text_utf8(input_path)
    # A file whose last line has no line end may have been cut short
    # (a copy or a download that stopped): `... 4.95` cut to `... 4`
    # still parses, as fee 4 (audit A2-1086). Say so; editors and the
    # writers here always end the last line.
    if _text and not _text.endswith(('\n', '\r')):
        _last = strip_tt_comment(_text.splitlines()[-1]).strip()
        if _last:
            emit_line(f"warning: {shown_name(input_path)}: the last line has no line "
                      f"end — if the file was cut short, its last number may "
                      f"be truncated; check it: {_last!r}", file=sys.stderr)
    with io.StringIO(_text) as f:
        for lineno, line in enumerate(f, 1):
            source = f"{shown_name(input_path)}:{lineno}"
            # An INKIND line values an in-kind move (`taxjson run` reads
            # it, lib/in_kind): checked here, never a row of the books.
            if parse_inkind_line(line, source) is not None:
                continue
            # A dated JOURNAL / RENAME is an event `taxjson run` books
            # (lib/dated_events): checked here, never a row of the file.
            if (parse_journal_line(line, source) is not None
                    or parse_rename_line(line, source) is not None):
                continue
            # Units held before the data, cost unknown (lib/
            # missing_history.read_tt_openings): read by `taxjson run`,
            # checked here, never a row of the file.
            if parse_unknown_opening_line(line, source) is not None:
                continue
            # A filing position against the loss rule (lib/loss_overrides):
            # read by `taxjson run`, never a row of the books.
            from taxjson.lib.loss_overrides import parse_line as _allowloss
            if _allowloss(line, source) is not None:
                continue
            # A line of the FX-on-cash ledger v2 (lib/cash_events: FXCONV,
            # CASHMOVE, CASHOPEN, CASHBAL, CASHBOOK): checked here, read by
            # `taxjson fx-cash --ledger v2`, never a row of the books.
            from taxjson.lib.cash_events import (CashLineError,
                                                 parse_line as _cashline)
            try:
                if _cashline(line, source) is not None:
                    continue
            except CashLineError as e:
                # Still refused (a typo'd line must not vanish), but said
                # for what it is: under the default ledger nothing reads
                # it (pre-release review).
                raise CashLineError(
                    f"{e} — a line of the FX-on-cash ledger v2 only "
                    f"(read with fx_cash_ledger = \"v2\" or `taxjson "
                    f"fx-cash --ledger v2`; the default ledger ignores "
                    f"it): fix it to the form shown, or delete it") from e
            try:
                expanded = expand_acquired(line)
            except ValueError as e:
                raise ValueError(f"{source}: {e}") from e
            for one in (expanded if expanded is not None else [line]):
                tx = parse_tt_line(one, account_name=account_name,
                                   source=source)
                if tx:
                    if equity:
                        _warn_bare_equity_symbol(tx, one, source)
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
    _key = source_key(input_path)
    for tx in transactions:
        tx['id'] = compute_tt_id(tx)
        # Provenance for cross-file dedup (not part of the id): two .tt
        # files holding the same line are separate records (R1-296);
        # a masked name carries a key so two files never share it
        # (A2-0159).
        tx['source'] = shown_name(input_path)
        if _key:
            tx['source_key'] = _key
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
        emit_line(
            f"note: skipped {skipped} transaction(s) with no .tt representation",
            file=sys.stderr,
        )
    if two_dates:
        which = ("SETTLEMENT" if date_basis == 'settle' else "TRADE")
        emit_line(
            f"note: {two_dates} row(s) have a trade date and a later "
            f"settlement date; a .tt line carries one date, so each was "
            f"written with its {which} date (--date-basis "
            f"{'trade' if date_basis == 'settle' else 'settle'} for the "
            f"other). The year a sale lands in follows that date.",
            file=sys.stderr,
        )
    if mults:
        emit_line(
            f"warning: {len(mults)} row(s) carry a contract multiplier the "
            f".tt format cannot hold ({', '.join(mults[:5])}"
            f"{' ...' if len(mults) > 5 else ''}); the total is kept, but "
            f"the re-imported row loses the declared size.",
            file=sys.stderr,
        )


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` through a temp file in the same folder:
    the old contents stay until the new ones are complete."""
    from taxjson.lib.cli_diag import write_text_atomic
    write_text_atomic(path, text)


@guard_main("taxjson-convert-tt")
def main():
    try:
        _main()
    except ValueError as exc:
        # Malformed .tt lines raise deliberately-loud ValueErrors;
        # surface them as clean CLI errors, not tracebacks
        # (2026-09 audit).
        emit_line(f"taxjson-convert-tt: error: {exc}", file=sys.stderr)
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
        emit_line(f"taxjson-convert-tt: error: input file not found: {input_path}",
                  file=sys.stderr)
        sys.exit(2)                 # a missing input (A2-0164)

    suffix = input_path.suffix.lower()
    if suffix == '.json':
        # JSON → tt. The one date a .tt line keeps is the tax-year date
        # basis, which is the country's: never a silent Canadian settle
        # default (partition INPUTS-14 — a US book came back with its
        # Dec-31 sales in January).
        date_basis = args.date_basis
        if date_basis is None:
            from taxjson.lib.country import CountryError
            from taxjson.lib.missing_history import tax_date_near
            try:
                date_basis = tax_date_near(input_path)
            except CountryError as e:
                emit_line(f"taxjson-convert-tt: error: taxjson.toml: {e}",
                          file=sys.stderr)
                sys.exit(2)
        if date_basis is None:
            data = json.loads(input_path.read_text(encoding='utf-8'))
            rows = (data.get('transactions', data)
                    if isinstance(data, dict) else data)
            if any(isinstance(t, dict) and t.get('date_settle')
                   and t.get('date') and t['date_settle'] != t['date']
                   for t in rows or []):
                emit_line("taxjson-convert-tt: error: rows have a trade date "
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
