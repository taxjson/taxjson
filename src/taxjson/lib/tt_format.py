"""The TaxText (.tt) format: a .tt line read into a taxjson row
(`parse_tt_line`, the sugar expanded first: `tt_to_json`), a row written
as its line (`tx_to_tt_line`, `json_to_tt_lines`), and the event lines
(JOURNAL, RENAME, INKIND, UNKNOWN_OPENING, NOTOWN) other modules read
(`tt_lines`, `strip_tt_comment`, `parse_journal_line`,
`parse_not_own_line`, ...). Each action's
field order: `_FORMS`, and docs/settings.md (".tt files").

The `taxjson-convert-tt` command is bin/taxjson_convert_tt.py. Moved from
bin (PLAN step 8): lib reads a .tt file without importing the command
code.
"""
from taxjson.project.toml_io import read_toml
from taxjson.lib.opening import opening_row
from taxjson.lib.stage_msg import emit_line
import difflib
import hashlib
import io
import json
import math
import re
import sys
from typing import Any, Dict, Optional
from datetime import datetime
from pathlib import Path

from taxjson.lib.futures_prefixes import FUTURES_PREFIXES, canonical_futures_symbol
from taxjson.lib.derivative_checks import _derivative_kind, _rename_derivative_check
from taxjson.model.evidence import (EvidenceError, commission_refunds_of_tt,
                                    commission_refunds_tt, corp_cash_of_tt, corp_cash_tt,
                                    settle_native_of_tt, settle_native_tt)
from taxjson.lib.row_types import (CASH_LEG_CASH, CASH_LEG_KINDS, CASH_LEG_NONE,
                                   CASH_LEG_REWARD, LISTING_JOURNAL_TYPE,
                                   LOT_MOVE_TYPE, OWN_MOVE_TYPE, REORG_356_TYPE,
                                   SPINOFF_355_TYPE, TRANSFER_CUSTODY_TYPE,
                                   TRANSFER_REWRITE_TYPE)
from taxjson.model import money as M
from taxjson.model.transaction import event_identity


_VALID_ACTIONS = (
    'BUYSELL', 'TRANSFER', 'SPLIT', 'ASSIGN', 'ADJUST', 'DISALLOW',
    'DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'OPENING',
)
# Line-level sugar expanded by tt_to_json before parse_tt_line, and
# INKIND (the value of an in-kind move between a taxable and a registered
# account: read by `tjs run`, lib/in_kind — never a row of the books).
_SUGAR_ACTIONS = ('ACQUIRED', 'INKIND')
# Dated events written date first (lib/dated_events): a JOURNAL between
# two listings of one security inside the account, and a ticker change
# (RENAME). `tjs run` reads them from the .tt files of every account
# and books them as events (the journal's transfer legs, the rename's
# SPLIT row in each account holding the old symbol) — they are checked
# here and never rows of the converted file.
_EVENT_ACTIONS = ('JOURNAL', 'RENAME', 'ALLOWLOSS', 'FXCONV', 'CASHMOVE',
                  'CASHOPEN', 'CASHBAL', 'CASHBOOK', 'NOTOWN')
# ALLOWLOSS (lib/loss_overrides) is a filing position against the loss
# rule on one sale of the account: read by `tjs run`, checked here,
# never a row of the books. NOTOWN (lib/transfer_in.NotOwn) undoes one
# pairing of a transfer-in with a transfer-out as a move of your own:
# read by `tjs run`, checked here, never a row of the books.
JOURNAL_FORM = "JOURNAL <date> <FROM> <TO> <qty> [separate]"
# The trailing word that marks a .tt JOURNAL line as a journal of its
# own, never a restatement of a broker's journal near it
# (lib/dated_events.Journal.separate).
JOURNAL_SEPARATE = "separate"
RENAME_FORM = "RENAME <date> <OLD> <NEW> [late=fold|late=separate]"
# The time an OPENING row is booked at: the start of the snapshot day,
# before anything else that day (the line itself has no time column).

# Highest token count per action (fields + optional trailing columns).
# A token past these used to be ignored without a word — a stray
# column is a typo to fix (notes belong after `#`).
_MAX_TOKENS = {
    'BUYSELL': 10, 'ASSIGN': 10, 'SPLIT': 6,
    'DIVIDEND': 9, 'DIVIDEND_IN_LIEU': 9, 'TAX': 9,
    'INTEREST': 5, 'FEE': 5, 'ADJUST': 6, 'DISALLOW': 6,
}
# Each row action's fields, the required ones first (docs/settings.md,
# ".tt files"): a short line is named with the fields it lacks — the
# reader indexed past its end and said "IndexError: list index out of
# range" (backlog TJ-0128).
_FORMS = {
    'BUYSELL': ('DATE TIME SYMBOL QTY CUR PRICE TOTAL', '[FEE] [xSIZE]'),
    'ASSIGN': ('DATE TIME SYMBOL QTY CUR PRICE TOTAL', '[FEE] [xSIZE]'),
    'TRANSFER': ('DATE TIME SYMBOL QTY CUR PRICE TOTAL', '[DECLARED]'),
    'SPLIT': ('DATE TIME OLD NEW RATIO', '[corp=EVENT election=NAME]'),
    'DIVIDEND': ('DATE TIME SYMBOL QTY CUR PRICE GROSS', '[NET] [facts]'),
    'DIVIDEND_IN_LIEU': ('DATE TIME SYMBOL QTY CUR PRICE GROSS',
                         '[NET] [facts]'),
    'TAX': ('DATE TIME SYMBOL QTY CUR PRICE GROSS', '[NET] [facts]'),
    'INTEREST': ('DATE TIME CUR AMOUNT', '[symbol=SYMBOL]'),
    'FEE': ('DATE TIME CUR AMOUNT', ''),
    'ADJUST': ('DATE TIME SYMBOL CUR AMOUNT',
               '[type=roc|dist] [record=YYYY-MM-DD]'),
    'DISALLOW': ('DATE TIME SYMBOL CUR AMOUNT', ''),
}
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TIME_RE = re.compile(r'^\d{2}:\d{2}:\d{2}$')
# Optional contract size after the fee on a BUYSELL/ASSIGN line: `x1000`
# (a CL futures option), `x50` (ES), `x0.1` (a micro crypto future) —
# audit S026-22. Without it an option line is checked at the equity 100
# and a futures line is not checked at all. The writers spell it in
# fixed-point decimals (size_text); an exponent (`x1e+06`, as v0.37.0
# and earlier wrote a size of a million) is read too (backlog TJ-0385).
_MULT_RE = re.compile(r'^[xX](\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$')
# Optional `exercise_of=<SHARES>` on an ASSIGN line: the line is a
# warrant's or a right's exercise leg, delivering that share listing —
# no disposition; the warrant's cost goes into the shares (core.
# exercise_target, tax-logic CA-OPT-09 / US-OPT-06). The broker parsers
# mark the leg; a json -> tt -> json round trip, or a pasted `tjs
# events` line, lost the mark and booked the warrants as a sale at 0
# (backlog TJ-0377).
EXERCISE_KEY = 'exercise_of'
# Optional `type=stock_dividend` on a BUYSELL line: the line is a stock
# dividend's new shares (row_types.is_stock_dividend), with its `ex=` /
# `record=` date when the broker gave one. The gains run applies the
# country's rule to it (US: §307 spreads the old shares' basis and
# keeps their dates, US-STKDIV-01..05; Canada: $0 until an ADJUST
# gives the declared amount). A json -> tt -> json round trip, or a
# pasted `tjs events` line, read it back as an ordinary purchase
# at $0: a US sale's gain moved (backlog TJ-0528).
STOCK_DIVIDEND_TOKEN = 'type=stock_dividend'
_STOCKDIV_DATE_KEYS = {'ex': 'ex_date', 'record': 'record_date'}
# Optional `delivered_cost=<amount>` on a `type=stock_dividend` line:
# the row is the obligation of a short covered before it (the account
# was short on the ex-dividend date), and this is what delivering the
# shares owed cost, in the line's currency. The gains engines book it
# as a cost of that short's cover (tax-logic CA-STKDIV-02 /
# US-STKDIV-06; the row's `delivered_cost`, no part of its id). On a
# row the engines do not read as a covered short's the run says it is
# not booked (backlog TJ-5282).
DELIVERED_KEY = 'delivered_cost'
# Optional `type=futures_settlement [settle=LEG,MONEY,PL,CUR[,RATE]]` on
# a BUYSELL line of a futures contract: the line is a part of a book
# already on the settlement basis (lib/futures: its total is the
# realized P/L, not qty x price), with the native facts its settlement
# recorded (TaxTransaction.settle_native: the leg LO/LC/SO/SC, the
# native money, the P/L, the currency, the rate) that the Canadian
# engine re-averages a contract held in several accounts from and keeps
# a short's pool apart with. A json -> tt -> json round trip dropped
# both and read the parts back as raw fills: the pooled gain changed
# (backlog TJ-0662).
FUTURES_SETTLEMENT_TYPE = 'futures_settlement'
FUTURES_SETTLEMENT_TOKEN = f'type={FUTURES_SETTLEMENT_TYPE}'
# Optional `type=trade_cancel` on a BUYSELL / ASSIGN line: the line is a
# broker's cancellation of a trade (IB code `Ca`, lib/trade_cancel), the
# original's reverse. taxjson-merge2 drops it with the original it
# cancels, from any input of the account. A json -> tt -> json round
# trip wrote it as an ordinary trade: a cancelled sale stayed booked,
# its reverse a purchase at the sale price (backlog TJ-0985).
TRADE_CANCEL_TOKEN = 'type=trade_cancel'
SETTLE_KEY = 'settle'
# Optional `type=<kind>` for the row kinds `tjs run` makes and an engine
# gives meaning to (lib/row_types), so a book of them converted to .tt
# and back is the same book: a json -> tt -> json round trip wrote each
# as an ordinary line, and a section 355 spin-off's shares read back as
# a purchase, a section 356 exchange as a sale and a purchase, a custody
# move as an acquisition (backlog TJ-5210). taxjson-convert-tt and `tjs
# events` write them; the reader takes them on these lines only:
# * a BUYSELL line (RUN_TRADE_TYPES): `type=spinoff_355 [ex=DATE]
#   [record=DATE]` (the spun-off shares; QTY positive) and
#   `type=reorg_356 [boot=AMOUNT]` (the old shares' sale, with the boot
#   the engine reads from the row's `gross_amount`, and the new shares'
#   purchase), each with its `corp=`; `type=transfer_custody` and
#   `type=transfer_rewrite` (a sheltered account's transfer row in the
#   loss-rule context);
# * an ADJUST line: `type=spinoff_355` with its `corp=` (the parent's
#   basis moved to the spun-off shares);
# * a TRANSFER line (RUN_TRANSFER_TYPES): `type=listing_journal
#   pair=<id>` (a leg of a journal between two listings: the row's
#   `journal_pair`) and `type=own_account_move` (a netted move between
#   two registered accounts, balance only).
# A TRANSFER row of type lot_move is paired with its other account's
# leg by its description, which no line carries: the writer refuses it.
RUN_TRADE_TYPES = (SPINOFF_355_TYPE, REORG_356_TYPE, TRANSFER_CUSTODY_TYPE,
                   TRANSFER_REWRITE_TYPE)
RUN_TRANSFER_TYPES = (LISTING_JOURNAL_TYPE, OWN_MOVE_TYPE)
BOOT_KEY = 'boot'
PAIR_KEY = 'pair'
_PAIR_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
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
# Optional corporate-action provenance at the end of a trade, income or
# ADJUST line: the event and election that emitted the row, and an s.86.1
# parent cost reduction's share count. The engine keys on them (an s.86.1
# reduction after the parent was sold, a spin-off's deferred wash, a cost
# move that is no return of capital); a json -> tt -> json round trip
# dropped them, so a reduction read as a plain return of capital (backlog
# TJ-1117). `cash=`: the cash the event paid (cash in lieu, boot), which
# fx-cash reads, as `<amount>:<CUR>[:@<paid>]` joined by `;` (the row's
# `corp_cash` with no spaces, model/evidence `corp_cash_tt`; backlog
# TJ-1175).
_CORP_KEYS = {'corp': 'corp_event_id', 'election': 'corp_election',
              'parent_qty': 'corp_parent_qty', 'cash': 'corp_cash'}
_CORP_ACTIONS = ('BUYSELL', 'ASSIGN') + _FACT_ACTIONS
# A SPLIT line's: the event and its election only (no money moves on it).
_SPLIT_CORP_KEYS = ('corp', 'election')
# Optional cash facts at the end of a trade, income or ADJUST line, which
# the FX-on-cash ledgers read (lib/cash_legs, lib/fx_cash_v2). A parser's
# row says them by its wording and its source file, which a .tt line
# does not keep: a json -> tt -> json round trip read a crypto-for-crypto
# leg and an in-kind reward back as cash trades, a broker's cash return
# of capital as no cash, and a refunded commission as paid net on the
# trade's date (backlog TJ-5036).
# `cash_leg=` (the row's `cash_leg`, row_types CASH_LEG_KINDS): `none`,
# the line's amount is a value and no cash moved; `reward`, a reward
# paid in the line's SYMBOL (cash only where that symbol is); `cash`,
# the amount was paid in cash (an ADJUST of type=roc a broker paid, a
# corp= line's own cash leg). Without it a line reads as it always did:
# a trade or an income line moved its amount in cash (a corp= line
# none but its cash=), an ADJUST none.
# `refunds=` (a BUYSELL / ASSIGN line; the row's `commission_refunds`):
# the commission refunds the line's TOTAL is net of, `<date>:<amount>`
# joined by `;`: the commission left in full on the line's date and
# each refund came in on its own.
CASH_LEG_KEY = 'cash_leg'
REFUNDS_KEY = 'refunds'
_CASH_KEYS = {CASH_LEG_KEY: 'cash_leg', REFUNDS_KEY: 'commission_refunds'}
# The lines that take each `cash_leg=` value (_cash_leg_problem).
_CASH_LEG_NONE_ACTIONS = ('BUYSELL', 'ASSIGN', 'DIVIDEND', 'DIVIDEND_IN_LIEU',
                          'TAX')
_CASH_LEG_REWARD_ACTIONS = ('BUYSELL', 'DIVIDEND')
_REFUND_ACTIONS = ('BUYSELL', 'ASSIGN')
# The tokens parse_tt_line takes off a line for _check_corp.
_TAIL_KEYS = (*_CORP_KEYS, *_CASH_KEYS)
# Optional `settled=YYYY-MM-DD` at the end of any row line with a time
# column: the row's settlement date, when it is not the line's date. A
# line has one date, read as the trade (or pay) date and the settlement
# date alike; an income row is written with the day it was paid
# (TJ-0862) and a trade-basis book's trade with its trade date, so the
# day the cash settled was lost, and the FX-on-cash ledgers, which walk
# a row's cash on its settlement date, walked it on another day, in
# another year across a year end (backlog TJ-5211). On or after the
# line's date; it is the row's `date_settle`, part of its id.
SETTLED_KEY = 'settled'
# Optional `traded=YYYY-MM-DD`, the other way round: the row's trade
# date, when the line's date is its settlement date (a trade written on
# the settle basis, Canada's). On or before the line's date; it is the
# row's `date`, part of its id, and the line's date stays the row's
# `date_settle`. Read back without it the trade date was the settlement
# date: `tjs t1135`'s cash on trade dates moved a day or two (backlog
# TJ-5213). A line carries one of the two, never both.
TRADED_KEY = 'traded'
# The most days a `settled=` or a `traded=` date may be from its line's
# date: a longer gap is a typo'd year, never a settlement cycle (the
# reader refuses it; the writer has no line for such a row).
MAX_DATE_GAP_DAYS = 366
# `not_tt=<why>`: what the `tjs events` view ends a line with when no
# .tt line can say the row (NOT_TT_WHY: taxjson-convert-tt refuses such
# a row). Never read as a row: pasted into a .tt file the line is
# refused, saying why (backlog TJ-5212).
NOT_TT_KEY = 'not_tt'
NOT_TT_WHY = {
    'in_kind': (
        "a move in kind that `tjs run` booked from the account's transfer "
        "rows (securities valued, no cash paid): as a .tt row it would "
        "read as a trade for cash. Keep the account's transfer rows and "
        "the move's INKIND line or transfer settings"),
    'cash_leg': (
        "a row whose amount did not move as cash the way the line would "
        "say it did (a reward's or a coin fee's INTEREST, FEE or TAX "
        "row: no cash_leg= token on that line says it). Keep the row in "
        "its export"),
    'dates': (
        "a row whose two dates a line cannot say (it settles before the "
        "day it trades or is paid, or more than 366 days after). Fix "
        "the row's dates in its export or book"),
    'row_type': (
        "a row whose kind (its `type`) no .tt line carries: read back "
        "it would be an ordinary row, booked by another rule. Keep the "
        "row in the book `tjs run` made it in"),
}
_LINE_KEY_RE = re.compile(rf'^({SETTLED_KEY}|{TRADED_KEY}|{NOT_TT_KEY})=(\S*)$')
# Optional `symbol=<SYMBOL>` at the end of an INTEREST line: the
# property the interest is paid on (a foreign bond's coupon), the
# income of that property's T1135 row (tax-logic CA-RPT-14). Without it
# the line is interest on the account's cash (`CASH`). A json -> tt ->
# json round trip wrote no symbol and read the coupon back as CASH, so
# the bond's T1135 income fell to 0 (backlog TJ-0856).
INTEREST_SYMBOL_KEY = 'symbol'
# Actions dated the day they were PAID, whatever the date basis: income,
# withholding and fees (the gains engines file them by their pay date,
# pipeline in_year; CA-DATE-11). A settle-basis export wrote a dividend
# paid Dec 31 that settles Jan 2 with its January date, moving it into
# the next year on re-import (backlog TJ-0862).
PAID_DATE_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST',
                     'FEE')
_FACT_RE = re.compile(r'^([a-z_]+)=(\S+)$')
_ADJUST_TYPES = ('roc', 'dist')


def strip_tt_comment(line: str) -> str:
    """Drop an inline `# ...` comment. Before, `BUYSELL ... # note`
    tokenised the note as data, and a `# DECLARED` remark on a TRANSFER
    silently granted the attestation token."""
    i = line.find('#')
    return line if i < 0 else line[:i]


def tt_lines(text: str) -> list:
    """A .tt file's lines as `tt_to_json` reads and numbers them (and an
    editor shows them): split at a line feed only — read_text has made
    \\r\\n and \\r one — without the line ends, no final empty piece.
    str.splitlines also splits at a form feed, a vertical tab, U+001C-
    U+001E, U+0085, U+2028 and U+2029 inside a line: a reader that used
    it numbered every later line differently from the books and read
    the text after one in a comment as a line of its own (check-tt,
    backlog TJ-0029; the run's declared-line readers booked a RENAME,
    an ALLOWLOSS or a cash line there). Every reader of a .tt file
    splits it here."""
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines


def _where(source: str) -> str:
    return f"{source}: " if source else ''


class _LineRefusal(ValueError):
    """A refusal raised inside parse_tt_line's row parse that already
    names its place and the line: passed on as it is, never wrapped in
    the generic "malformed .tt line (…)" text, which named the place
    twice (backlog TJ-0207)."""


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
                f"units held before the data, read by `tjs run` "
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
    trade_keys = {}
    corp = _take_line_keys(parts, line, source, action)
    if action in _FACT_ACTIONS:
        while parts and _FACT_RE.match(parts[-1]):
            key, val = _FACT_RE.match(parts.pop()).groups()
            _refuse_twice(key, val, facts, line, source)
            facts[key] = val
        corp.update({k: facts.pop(k) for k in _TAIL_KEYS if k in facts})
        facts = _check_facts(action, facts, line, source, 'corp' in corp)
    elif action == 'INTEREST':
        # `symbol=<SYMBOL>`: the property the interest is paid on.
        while len(parts) > 5 and _FACT_RE.match(parts[-1]):
            key, val = _FACT_RE.match(parts.pop()).groups()
            _refuse_twice(key, val, facts, line, source)
            if key != INTEREST_SYMBOL_KEY:
                raise ValueError(
                    f"{_where(source)}unknown .tt token {key}={val} — an "
                    f"INTEREST line takes {INTEREST_SYMBOL_KEY}=<SYMBOL> "
                    f"(the property it is paid on, such as a bond): "
                    f"{line.strip()!r}")
            facts[key] = val
    elif action in ('BUYSELL', 'ASSIGN'):
        # `key=value` tokens past the total (`exercise_of=`), in any
        # order with the fee and the `x<size>` token.
        kept = parts[:8]
        for tok in reversed(parts[8:]):
            m = _FACT_RE.match(tok)
            if m is None:
                kept.insert(8, tok)
                continue
            _refuse_twice(m.group(1), m.group(2), trade_keys, line, source)
            trade_keys[m.group(1)] = m.group(2)
        parts = kept
        corp.update({k: trade_keys.pop(k) for k in _TAIL_KEYS if k in trade_keys})
    elif action == 'TRANSFER':
        trade_keys = _take_transfer_keys(parts, line, source)
    # A futures part already on the settlement basis: its total is the
    # realized P/L, not qty x price (backlog TJ-0662).
    _settled = trade_keys.get('type') == FUTURES_SETTLEMENT_TYPE
    _max = _MAX_TOKENS.get(action)
    if _max is not None and len(parts) > _max:
        raise ValueError(
            f"{_where(source)}{action} row has {len(parts) - _max} "
            f"unexpected trailing token(s) {parts[_max:]} — put notes "
            f"after `#`: {line.strip()!r}")
    _form = _FORMS.get(action)
    if _form is not None and len(parts) < 1 + len(_form[0].split()):
        _fields = _form[0].split()
        _missing = _fields[len(parts) - 1:]
        _named = (" and ".join(_missing) if len(_missing) < 3 else
                  ", ".join(_missing[:-1]) + " and " + _missing[-1])
        raise ValueError(
            f"{_where(source)}{action} needs {_named} after "
            f"{_fields[len(parts) - 2]} ({action} {_form[0]}"
            f"{' ' + _form[1] if _form[1] else ''}): {line.strip()!r}")

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
            tx['symbol'] = _book_symbol(parts[3])
            if action == 'SPLIT':
                tx['symbol_new'] = _book_symbol(parts[4])
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
                        if not (math.isfinite(_declared) and _declared > 0):
                            raise _LineRefusal(
                                f"{_where(source)}contract size "
                                f"{parts[-1]!r} must be > 0 (the units "
                                f"one contract covers, such as x100): "
                                f"{line.strip()!r}")
                        parts = parts[:-1]
                    if len(parts) > 9:
                        raise _LineRefusal(
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
                # (the run reads this warning back: lib/tt_totals)
                _is_fut = tx['symbol'].startswith(FUTURES_PREFIXES)
                # The project's ticker.map MULT line first, as the
                # engine sizes the contract (contract_size.equity_option_size):
                # a mini option's correct total was a typo at x100
                # (backlog TJ-0864).
                _mult = _tt_contract_size(tx)
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
                if _settled:
                    pass        # the total is the realized P/L
                elif (_q < 0 and _expected < 0 and tx['price'] > 0
                        and abs(tx['net_amount']) < 0.005
                        and (not _is_fut or tx.get('multiplier'))):
                    emit_line(
                        f"warning: {_where(source)}.tt sell total 0 on a "
                        f"sale whose commission {M.shown(_fee, 2):.2f} exceeds its "
                        f"gross {M.shown(abs(_q) * tx['price'] * _mult, 2):.2f}: the "
                        f"proceeds are {M.shown(_expected, 2):.2f}, and 0 leaves "
                        f"{M.shown(-_expected, 2):.2f} of commission out of the loss "
                        f"— write the negative total ({M.shown(_expected, 2):.2f}): "
                        f"{line.strip()!r}", file=sys.stderr)
                elif (tx['price'] > 0 and abs(_q) > 0
                        # A futures line is checked only with its size
                        # on the line (`x1000`).
                        and (not _is_fut or tx.get('multiplier'))
                        and abs(_total - _expected) >
                        tolerance(_expected)
                        and not _adjusted_series_total(tx, _total)):
                    emit_line(
                        f"warning: {_where(source)}.tt line total "
                        f"{M.shown(_total, 2):.2f} differs from "
                        f"qty*price{f'*{_mult:g}' if _mult != 1 else ''}"
                        f"{'+' if _q > 0 else '-'}fee = "
                        f"{M.shown(_expected, 2):.2f} by more than 1%: {line.strip()!r} "
                        f"— check for a typo (the total IS what the engine "
                        f"books as cost/proceeds).",
                        file=sys.stderr,
                    )

        elif action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'):
            tx['symbol'] = _book_symbol(parts[3])
            tx['quantity'] = _tt_num(parts[4])
            tx['currency'] = parts[5].upper()
            # The per-share amount (PRICE): never read, the row booked
            # price 0 and `tjs divs` showed a rate of 0 (backlog
            # TJ-0984).
            tx['price'] = _tt_num(parts[6])
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
            tx['symbol'] = (_book_symbol(facts.pop(INTEREST_SYMBOL_KEY, ''))
                            or 'CASH')

        elif action == 'ADJUST' or action == 'DISALLOW':
            tx['symbol'] = _book_symbol(parts[3])
            tx['currency'] = parts[4].upper()
            tx['net_amount'] = _tt_num(parts[5])
    except _LineRefusal:
        raise
    except (ValueError, IndexError) as e:
        # The reason and the line as written: never the Python
        # exception's name or a repr with its line end (TJ-0128).
        raise ValueError(
            f"{_where(source)}taxjson-convert-tt: malformed .tt line "
            f"({e}). Action={action!r}, line={line.strip()!r}. Hand-edit "
            f"or remove the row before re-running — silent skip would "
            f"lose a transaction the engine downstream needs."
        ) from e

    # A SPLIT ratio of 0 or below is never a corporate action: refused
    # here, at its line, as the books' loader refuses it (core.
    # coerce_transaction_row) — that refusal named the converted work
    # file, and `tjs check-tt` said the line parses (TJ-0006).
    if action == 'SPLIT' and not tx['quantity'] > 0:
        raise ValueError(
            f"{_where(source)}SPLIT ratio must be > 0 (got {parts[5]}) — "
            f"a zero or negative ratio is never a real corporate action "
            f"(RATIO is new shares per old: 0.1 for one-for-ten): {line.strip()!r}")

    if action in ('BUYSELL', 'ASSIGN') and not _settled:
        _check_total_sign(action, tx, parts, 'type' in trade_keys, line,
                          source)

    tx.update(_check_line_keys(action, tx, trade_keys, 'corp' in corp,
                               parts, line, source))
    # Not part of the id (as in TaxTransaction.compute_id).
    tx.update(facts)
    tx.update(_check_corp(action, corp, line, source, tx))
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
    3). `tjs run` books it as the move's two transfer legs and
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
    _sy = {'action': 'SPLIT', 'symbol': _book_symbol(frm),
           'symbol_new': _book_symbol(to)}
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
# missing history, read by `tjs run`).
UNKNOWN_OPENING_FORM = ('OPENING <date> <SYMBOL> <qty> cost=unknown '
                        '[reason="..."]')


def _cut_comment_outside_quotes(line: str) -> str:
    """`line` up to its first `#` outside quotes (a reason="..." may
    hold a `#`), read as the shell-quoting the line is then split with
    (shlex, POSIX): a backslash outside quotes escapes the next
    character, inside double quotes it escapes `"` and `\\`, and single
    quotes hold everything literally. An escaped quote toggled the old
    count, so `reason="statement \\"#2\\""` was cut at its `#` and
    refused as an unclosed quote (backlog TJ-0537)."""
    quote = ''
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote == '"':
            if ch == '\\' and i + 1 < n and line[i + 1] in '"\\':
                i += 2
                continue
            if ch == '"':
                quote = ''
        elif quote == "'":
            if ch == "'":
                quote = ''
        elif ch == '\\':
            i += 2
            continue
        elif ch in '"\'':
            quote = ch
        elif ch == '#':
            return line[:i]
        i += 1
    return line


def parse_unknown_opening_line(line: str, source: str = ''):
    """`OPENING <date> <SYMBOL> <qty> cost=unknown [reason="..."]` ->
    {date, symbol, quantity, reason, line}: <qty> units of SYMBOL held
    on <date>, bought before the data starts at a cost the files do not
    give (a negative <qty>: a short sold before the data, its proceeds
    unknown) — missing history (tax-logic
    CA-ACB-11 / US-BASIS-04). None when the line is not an OPENING line
    with a `cost=` word (a positions-report OPENING line has a currency
    and a total instead). `tjs run` reads it from the account's .tt
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
    _sy = {'action': 'SPLIT', 'symbol': _book_symbol(sym)}
    _canonical_ca_symbols(_sy)
    sym = _sy['symbol']
    if sym.startswith(FUTURES_PREFIXES):
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
    # A negative quantity: a short held before the data (TJ-0844).
    if not q or abs(q) > _MAX_EVENT_QTY:
        raise ValueError(
            f"{where}malformed OPENING line — the quantity must be a "
            f"number of units held before the data, negative for a short "
            f"(expected `{UNKNOWN_OPENING_FORM}`): {shown!r}")
    if q < 0 and _derivative_kind(sym):
        raise ValueError(
            f"{where}OPENING cost=unknown with a negative quantity is for "
            f"a short of shares, not {_derivative_kind(sym)} ({sym}): "
            f"`tjs find-missing-history --include-options` lists a "
            f"contract written before the data: {shown!r}")
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


def parse_rename_line(line: str, source: str = ''):
    """`RENAME <date> <OLD> <NEW> [late=fold|late=separate]` -> {date,
    old, new, late}: a ticker change on that date (lib/renames: the
    position, cost and acquisition dates carry from OLD to NEW), or None
    when the line is not a RENAME line. `tjs run` books it in every
    account whose books carry OLD (lib/dated_events). Two spellings of
    one listing in the books (A.TO / A.CN: a Canadian venue folds into
    .TO) return the line with `noop`, the Info line to say: nothing is
    booked (a SPLIT would strand the pool). Raises ValueError
    naming the form on a malformed line — the ticker.map form `RENAME
    OLD NEW YYYY-MM-DD` included (a .tt line is date first)."""
    from taxjson.lib.rename_decl import parse_rename_tail
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
    _sy = {'action': 'SPLIT', 'symbol': _book_symbol(parts[2]),
           'symbol_new': _book_symbol(parts[3])}
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
        _refuse_twice(k, v, facts, line, source)
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
        return {'date': day, 'symbol': _book_symbol(sym), 'quantity': qty,
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
    from taxjson.lib.option_symbols import is_option_symbol
    # An option's price is per share of the contract; its size (a
    # ticker.map MULT line, a transfer row's multiplier) is known only
    # to the run, which values the line again at it (lib/in_kind.
    # apply_lines, `price`): the total here assumes the standard 100,
    # and a price given with a total is compared there (backlog TJ-0933).
    option = is_option_symbol(sym.upper())
    size = 100.0 if option else 1.0
    by_price = abs(qty) * price * size
    stated = total is not None
    if total is None:
        if price <= 0:
            raise ValueError(f"{where}INKIND line has no value — give the "
                             f"fair market value per share, or 0 and the "
                             f"total: {line.strip()!r}")
        total = by_price
    elif price > 0 and not option and abs(total - by_price) > max(
            0.05, 0.01 * max(by_price, 1.0)):
        raise ValueError(f"{where}INKIND total {M.shown(total, 2):.2f} differs from "
                         f"qty x price {M.shown(by_price, 2):.2f} — give one of them "
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
    return {'date': day, 'symbol': _book_symbol(sym), 'quantity': qty,
            'currency': cur.upper(), 'total': float(total), 'plan': plan,
            'source': source, 'price': float(price),
            'total_stated': stated}


def parse_opening_line(parts, line: str, account_name: str,
                       source: str) -> dict:
    """`OPENING <snapshot-date> <symbol> <qty> <currency> <total-cost>
    [<lot-date>] [x<size>]` -> an opening-balance row: a BUYSELL of type
    `opening` (row_types.OPENING_TYPE) dated the snapshot day at 00:00:00,
    with the lot's acquisition date in `lot_date`. It sets a position
    and its cost but is not a purchase (tax-logic CA-OPEN-01 /
    US-OPEN-01); `tjs opening` writes these lines from a positions
    report. Only a long position can be opened (a written option's or a
    short sale's premium depends on its write date: enter the write as
    a BUYSELL line). `x<size>`: an option's contract size other than
    100 (a mini option), as a BUYSELL line carries it — the units one
    contract delivers on its exercise (backlog TJ-0963)."""
    from taxjson.lib.option_symbols import is_option_symbol
    where = _where(source)
    for tok in parts[1:]:
        m = _LINE_KEY_RE.match(tok)
        if m is not None:
            raise ValueError(
                f"{where}{m.group(1)}= is not allowed on an OPENING line: "
                f"it has one day, the snapshot's (and no time column): "
                f"{line.strip()!r}")
    declared = None
    if len(parts) in (7, 8) and _MULT_RE.match(parts[-1]):
        declared = float(_MULT_RE.match(parts[-1]).group(1))
        if not (math.isfinite(declared) and declared > 0):
            raise ValueError(
                f"{where}contract size {parts[-1]!r} must be > 0 (the "
                f"units one contract covers, such as x10): "
                f"{line.strip()!r}")
        parts = parts[:-1]
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
    sym = _book_symbol(sym)
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
    if sym.startswith(FUTURES_PREFIXES):
        raise ValueError(
            f"{where}OPENING on a futures contract ({sym}) is not "
            f"supported: futures are booked on their own basis "
            f"(lib/futures.py). Enter the opening trade as a BUYSELL "
            f"line: {line.strip()!r}")
    if not re.match(r'^[A-Z]{3}$', cur.upper()):
        raise ValueError(
            f"{where}OPENING currency {cur!r} is not a three-letter code "
            f"(CAD, USD): {line.strip()!r}")
    if declared is not None and not is_option_symbol(sym):
        raise ValueError(
            f"{where}a contract size (x{size_text(declared)}) is an "
            f"option's: {sym} is not an option symbol: {line.strip()!r}")
    # The row's shape is lib/opening's, shared with the generic mapping's
    # `opening` target (its lot date and declared size included).
    tx = opening_row(symbol=sym, quantity=qty, total_cost=total, day=day,
                     currency=cur.upper(), account=account_name,
                     lot_date=lot, size=declared)
    _canonical_ca_symbols(tx)
    _warn_unknown_suffix(tx, line, source)
    tx['id'] = compute_tt_id(tx)
    return tx


def _book_symbol(tok: str) -> str:
    """A .tt line's symbol as the books spell it: upper case, a future's
    `/` or `\\` prefix `F:` (lib/futures_prefixes
    canonical_futures_symbol), as every broker parser writes it: a .tt
    `/QZZ6` was a pool apart from the broker's `F:QZZ6`, its id hashed
    from the spelling typed (backlog TJ-5150)."""
    return canonical_futures_symbol(str(tok).upper())


def _canonical_ca_symbols(tx: dict) -> None:
    """Spell a Canadian listing as every broker parser does: ROOT.TO
    with a dotted preferred series (base.canonical_ca_listing), and a
    future `F:` (_book_symbol). A .tt
    `ABC.V`, `ABC.VN` or `FTN.PRA.TO` used to stay its own ACB pool, so
    a loss sold here and the broker's repurchase of ABC.TO were never
    linked as identical property (audit A2-0300, A2-0635). A `.V` is
    Venture only on a CAD line (a SPLIT line has no currency: only the
    unambiguous .VN/.CN/.NE and undotted preferreds are folded)."""
    from taxjson.lib.brokerages.base import canonical_ca_listing
    from taxjson.lib.option_symbols import is_option_symbol
    cur = '' if tx.get('action') == 'SPLIT' else tx.get('currency', '')
    for key in ('symbol', 'symbol_new', EXERCISE_KEY):
        sym = canonical_futures_symbol(tx.get(key) or '')
        if sym:
            tx[key] = sym
        if (not sym or sym == 'CASH' or is_option_symbol(sym)
                or sym.startswith(FUTURES_PREFIXES)):
            continue
        canon = canonical_ca_listing(sym, cur)
        if canon:
            tx[key] = canon


def _take_line_keys(parts: list, line: str, source: str,
                    action: str = '') -> dict:
    """Take the tokens any row line may end with off `parts` (in place):
    `settled=<date>` or `traded=<date>`, returned as {key: date} for
    _check_corp (given twice: refused). A `not_tt=<why>` token is refused here: the
    line is the `tjs events` view's print of a row no .tt line can say
    (NOT_TT_WHY). A SPLIT line's `corp=` / `election=` too
    (_SPLIT_CORP_KEYS): the corporate event that made the row and its
    election. The event is part of the row's id (model/transaction
    `event_identity`), so a line without it read back as another row
    (backlog TJ-4985); any other `key=value` there is refused."""
    out: dict = {}
    for i in range(len(parts) - 1, 2, -1):
        m = _LINE_KEY_RE.match(parts[i])
        if m is None:
            continue
        key, val = m.groups()
        if key == NOT_TT_KEY and val not in NOT_TT_WHY:
            raise ValueError(
                f"{_where(source)}{NOT_TT_KEY}={val}: `tjs events` ends a "
                f"line with {NOT_TT_KEY}= when no .tt line can say the "
                f"row, and {val!r} is not one of its reasons "
                f"({', '.join(NOT_TT_WHY)}). The line is not a .tt row "
                f"either way: remove it: {line.strip()!r}")
        if key == NOT_TT_KEY:
            raise ValueError(
                f"{_where(source)}{NOT_TT_KEY}={val}: `tjs events` printed "
                f"this line for {NOT_TT_WHY[val]}, and remove this line: "
                f"{line.strip()!r}")
        _refuse_twice(key, val, out, line, source)
        out[key] = val
        del parts[i]
    while action == 'SPLIT' and len(parts) > 6 and _FACT_RE.match(parts[-1]) \
            and not _LINE_KEY_RE.match(parts[-1]):
        key, val = _FACT_RE.match(parts.pop()).groups()
        if key not in _SPLIT_CORP_KEYS:
            raise ValueError(
                f"{_where(source)}unknown .tt token {key}={val} — a SPLIT "
                f"line takes corp=<event id> and election=<name> (the "
                f"corporate action that made the row): {line.strip()!r}")
        _refuse_twice(key, val, out, line, source)
        out[key] = val
    return out


def _refuse_twice(key: str, val: str, seen: dict, line: str,
                  source: str) -> None:
    """A `key=value` token given twice on one line is refused, both
    values named: the reader kept the first one with no word, so
    `record=2024-12-31 record=2025-01-10` dated trust income in 2024 and
    `plan=own plan=rrsp` read as a move of your own (backlog TJ-0386).
    The tokens are read right to left: `val` is the earlier one."""
    if key in seen:
        raise ValueError(
            f"{_where(source)}{key}= is given twice ({key}={val} and "
            f"{key}={seen[key]}) — keep the one you mean: "
            f"{line.strip()!r}")


def _check_exercise(action: str, tx: dict, keys: dict, line: str,
                    source: str) -> str:
    """The `exercise_of=<SHARES>` token of a BUYSELL / ASSIGN line (the
    only key one takes), checked: an ASSIGN of a warrant or right,
    closing the warrants (negative QTY), delivering a share listing
    other than its own. Returns the listing, upper-cased."""
    from taxjson.lib.option_symbols import is_option_symbol
    where = _where(source)
    shown = line.strip()
    bad = sorted(set(keys) - {EXERCISE_KEY})
    if bad:
        raise ValueError(
            f"{where}unknown .tt token {bad[0]}={keys[bad[0]]} — a "
            f"BUYSELL or ASSIGN line takes {EXERCISE_KEY}=<SHARES> (a "
            f"warrant's or a right's exercise) or {STOCK_DIVIDEND_TOKEN} "
            f"[ex=DATE] [record=DATE] (a stock dividend's shares): "
            f"{shown!r}")
    target = _book_symbol(keys[EXERCISE_KEY])
    sym = str(tx.get('symbol') or '')
    if action != 'ASSIGN':
        raise ValueError(
            f"{where}{EXERCISE_KEY}= marks a warrant's or a right's "
            f"exercise leg: only an ASSIGN line takes it (`ASSIGN DATE "
            f"TIME WARRANT -QTY CUR 0 0 [FEE] [xSIZE] "
            f"{EXERCISE_KEY}=SHARES`): {shown!r}")
    if is_option_symbol(sym) or sym.startswith(FUTURES_PREFIXES):
        raise ValueError(
            f"{where}{EXERCISE_KEY}= is for a warrant or a right; an "
            f"option's (or a future's) exercise is paired by its own "
            f"symbol — remove the token: {shown!r}")
    if is_option_symbol(target) or target.startswith(FUTURES_PREFIXES):
        raise ValueError(
            f"{where}{EXERCISE_KEY}={target}: name the share listing the "
            f"exercise delivers (QZX.US), not an option or a future: "
            f"{shown!r}")
    from taxjson.lib.brokerages.base import canonical_ca_listing
    cur = str(tx.get('currency') or '')
    if ((canonical_ca_listing(target, cur) or target)
            == (canonical_ca_listing(sym, cur) or sym)):
        raise ValueError(
            f"{where}{EXERCISE_KEY}={target} is the line's own symbol — "
            f"name the share listing the warrants are exercised into: "
            f"{shown!r}")
    if not float(tx.get('quantity') or 0.0) < 0:
        raise ValueError(
            f"{where}a warrant exercise ({EXERCISE_KEY}=) closes the "
            f"warrants: its QTY is negative (the warrants given up): "
            f"{shown!r}")
    return target


_TRADE_CANCEL_TYPE = TRADE_CANCEL_TOKEN.split('=', 1)[1]


def _check_trade_cancel(action: str, tx: dict, keys: dict, line: str,
                        source: str) -> dict:
    """The `type=trade_cancel` token of a BUYSELL / ASSIGN line (with
    no other key but an ASSIGN's `exercise_of=`), checked: a
    cancellation reverses a fill, so its QTY is not 0. Returns the
    fields it sets on the row."""
    from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE
    shown = line.strip()
    bad = sorted(set(keys) - {'type', EXERCISE_KEY})
    if bad:
        raise ValueError(
            f"{_where(source)}unknown .tt token {bad[0]}={keys[bad[0]]} — "
            f"a broker's trade cancellation takes {TRADE_CANCEL_TOKEN} "
            f"(and an exercise's {EXERCISE_KEY}=): {shown!r}")
    if not abs(float(tx.get('quantity') or 0.0)) > 0:
        raise ValueError(
            f"{_where(source)}a trade cancellation ({TRADE_CANCEL_TOKEN}) "
            f"reverses a fill: its QTY is the original's, negated, not 0: "
            f"{shown!r}")
    out = {'type': TRADE_CANCEL_TYPE}
    if EXERCISE_KEY in keys:
        out[EXERCISE_KEY] = _check_exercise(
            action, tx, {EXERCISE_KEY: keys[EXERCISE_KEY]}, line, source)
    return out


def _check_stock_dividend(action: str, tx: dict, keys: dict, line: str,
                          source: str) -> dict:
    """The `type=stock_dividend [ex=DATE] [record=DATE]` tokens of a
    BUYSELL line, checked: the new shares of a stock dividend (a
    positive QTY of a share listing, not an option or a future). Returns
    the fields they set on the row."""
    from taxjson.lib.row_types import STOCK_DIVIDEND
    from taxjson.lib.option_symbols import is_option_symbol
    where = _where(source)
    shown = line.strip()
    if keys.get('type') != STOCK_DIVIDEND:
        raise ValueError(
            f"{where}type={keys.get('type')}: a BUYSELL line's only types are "
            f"{STOCK_DIVIDEND} (a stock dividend's new shares), "
            f"{FUTURES_SETTLEMENT_TYPE} (a settled futures part) and "
            f"{_TRADE_CANCEL_TYPE} (a broker's trade cancellation), and "
            f"the kinds `tjs run` makes, as taxjson-convert-tt writes "
            f"them ({', '.join(RUN_TRADE_TYPES)}): {shown!r}")
    bad = sorted(set(keys) - {'type', DELIVERED_KEY} - set(_STOCKDIV_DATE_KEYS))
    if bad:
        raise ValueError(
            f"{where}unknown .tt token {bad[0]}={keys[bad[0]]} — a stock "
            f"dividend's line takes {STOCK_DIVIDEND_TOKEN} [ex=DATE] "
            f"[record=DATE] [{DELIVERED_KEY}=AMOUNT]: {shown!r}")
    sym = str(tx.get('symbol') or '')
    if action != 'BUYSELL':
        raise ValueError(
            f"{where}{STOCK_DIVIDEND_TOKEN} marks a stock dividend's new "
            f"shares: only a BUYSELL line takes it (`BUYSELL DATE TIME "
            f"SYMBOL QTY CUR 0 0 {STOCK_DIVIDEND_TOKEN}`): {shown!r}")
    if is_option_symbol(sym) or sym.startswith(FUTURES_PREFIXES):
        raise ValueError(
            f"{where}{STOCK_DIVIDEND_TOKEN} is for shares a stock dividend "
            f"pays, not an option or a future: {shown!r}")
    if not float(tx.get('quantity') or 0.0) > 0:
        raise ValueError(
            f"{where}a stock dividend ({STOCK_DIVIDEND_TOKEN}) pays new "
            f"shares: its QTY is positive: {shown!r}")
    out: dict = {'type': STOCK_DIVIDEND}
    for key, field in _STOCKDIV_DATE_KEYS.items():
        if key not in keys:
            continue
        if not _is_date(keys[key]):
            raise ValueError(
                f"{where}{key}={keys[key]} is not a date (YYYY-MM-DD): "
                f"{shown!r}")
        out[field] = keys[key]
    if DELIVERED_KEY in keys:
        try:
            cost = _tt_num(keys[DELIVERED_KEY])
        except ValueError:
            cost = -1.0
        if not cost > 0:
            raise ValueError(
                f"{where}{DELIVERED_KEY}={keys[DELIVERED_KEY]}: what "
                f"delivering the shares a covered short owed cost, a "
                f"number above 0 in the line's currency (leave the token "
                f"out when it cost nothing): {shown!r}")
        out['delivered_cost'] = cost
    return out


def _check_line_keys(action: str, tx: dict, keys: dict, has_corp: bool,
                     parts: list, line: str, source: str) -> dict:
    """The `key=value` tokens past the fields of a BUYSELL, ASSIGN or
    TRANSFER line (`keys`; none on any other), checked by the kind the
    line's `type=` names: the fields they set on the row. A TRANSFER
    line's price is checked here too (`parts`: the line's tokens)."""
    typ = keys.get('type')
    if action == 'TRANSFER':
        _refuse_negative_transfer_price(tx, parts[6], line, source)
        return _check_transfer_type(tx, keys, line, source)
    if typ == FUTURES_SETTLEMENT_TYPE:
        return _check_futures_settlement(action, tx, keys, line, source)
    if typ == _TRADE_CANCEL_TYPE:
        return _check_trade_cancel(action, tx, keys, line, source)
    if typ in RUN_TRADE_TYPES:
        return _check_run_type(action, tx, keys, has_corp, line, source)
    if 'type' in keys:
        return _check_stock_dividend(action, tx, keys, line, source)
    if keys:
        return {EXERCISE_KEY: _check_exercise(action, tx, keys, line,
                                              source)}
    return {}


def _check_run_type(action: str, tx: dict, keys: dict, has_corp: bool,
                    line: str, source: str) -> dict:
    """The `type=` token of a BUYSELL line for a row kind `tjs run`
    makes (RUN_TRADE_TYPES), checked, with the tokens that kind takes:
    a spin-off's `ex=` / `record=` dates, a section 356 sale's `boot=`.
    Returns the fields they set on the row (backlog TJ-5210)."""
    where = _where(source)
    shown = line.strip()
    typ = keys['type']
    takes = {SPINOFF_355_TYPE: tuple(_STOCKDIV_DATE_KEYS),
             REORG_356_TYPE: (BOOT_KEY,)}.get(typ, ())
    bad = sorted(set(keys) - {'type'} - set(takes))
    if bad:
        raise ValueError(
            f"{where}unknown .tt token {bad[0]}={keys[bad[0]]} — a "
            f"type={typ} line takes "
            f"{' '.join(f'[{k}=...]' for k in takes) or 'no other token'}"
            f": {shown!r}")
    if action != 'BUYSELL':
        raise ValueError(
            f"{where}type={typ} marks a row `tjs run` made: only a "
            f"BUYSELL line takes it: {shown!r}")
    qty = float(tx.get('quantity') or 0.0)
    if not abs(qty) > 0:
        raise ValueError(
            f"{where}a type={typ} line moves shares: its QTY is not 0: "
            f"{shown!r}")
    out: dict = {'type': typ}
    if typ in (SPINOFF_355_TYPE, REORG_356_TYPE) and not has_corp:
        raise ValueError(
            f"{where}type={typ} marks a row of a corporate action: the "
            f"line needs the event it belongs to (corp=<event id>, as "
            f"taxjson-convert-tt writes it; the engine joins the event's "
            f"rows by it): {shown!r}")
    if typ == SPINOFF_355_TYPE and not qty > 0:
        raise ValueError(
            f"{where}type={typ} marks the shares a section 355 spin-off "
            f"delivered: its QTY is positive: {shown!r}")
    for key, field in _STOCKDIV_DATE_KEYS.items():
        if key not in keys:
            continue
        if not _is_date(keys[key]):
            raise ValueError(
                f"{where}{key}={keys[key]} is not a date (YYYY-MM-DD): "
                f"{shown!r}")
        out[field] = keys[key]
    if BOOT_KEY in keys:
        try:
            boot = _tt_num(keys[BOOT_KEY])
        except ValueError:
            boot = -1.0
        if not boot >= 0 or not qty < 0:
            raise ValueError(
                f"{where}{BOOT_KEY}={keys[BOOT_KEY]}: the cash (boot) a "
                f"section 356 exchange paid, a number of 0 or more, on "
                f"the old shares' line (QTY negative): {shown!r}")
        out['gross_amount'] = boot
    return out


def _take_transfer_keys(parts: list, line: str, source: str) -> dict:
    """Take a TRANSFER line's `key=value` tokens past its 8 fields off
    `parts` (in place): {key: value}; one given twice is refused."""
    keys: dict = {}
    for i in range(len(parts) - 1, 7, -1):
        m = _FACT_RE.match(parts[i])
        if m is None:
            continue
        _refuse_twice(m.group(1), m.group(2), keys, line, source)
        keys[m.group(1)] = m.group(2)
        del parts[i]
    return keys


def _check_transfer_type(tx: dict, keys: dict, line: str,
                         source: str) -> dict:
    """The `type=` token of a TRANSFER line (RUN_TRANSFER_TYPES) and a
    listing journal's `pair=`, checked. Returns the fields they set on
    the row; {} for a line with neither (backlog TJ-5210)."""
    if not keys:
        return {}
    where = _where(source)
    shown = line.strip()
    form = (f"type={LISTING_JOURNAL_TYPE} {PAIR_KEY}=<id> (a leg of a "
            f"journal between two listings) or type={OWN_MOVE_TYPE} (a "
            f"netted move between two registered accounts), the rows "
            f"`tjs run` makes, as taxjson-convert-tt writes them")
    typ = keys.get('type')
    if typ == LOT_MOVE_TYPE:
        raise ValueError(
            f"{where}type={typ}: a move between two of your taxable "
            f"accounts is two rows `tjs run` pairs by their description, "
            f"which a .tt line does not carry: no line says it. Remove "
            f"the line; the run books the move from the accounts' "
            f"transfer rows: {shown!r}")
    bad = sorted(set(keys) - {'type', PAIR_KEY})
    if bad or typ not in RUN_TRANSFER_TYPES:
        tok = (f"{bad[0]}={keys[bad[0]]}" if bad else
               f"type={typ}" if typ is not None else
               f"{PAIR_KEY}={keys[PAIR_KEY]}")
        raise ValueError(
            f"{where}unknown .tt token {tok} — a TRANSFER line takes "
            f"DECLARED, and {form}: {shown!r}")
    if not abs(float(tx.get('quantity') or 0.0)) > 0:
        raise ValueError(
            f"{where}a type={typ} line moves units: its QTY is not 0: "
            f"{shown!r}")
    out = {'type': typ}
    pair = keys.get(PAIR_KEY)
    if (typ == LISTING_JOURNAL_TYPE) != (pair is not None) or (
            pair is not None and not _PAIR_RE.match(pair)):
        raise ValueError(
            f"{where}a TRANSFER line takes {form}: {PAIR_KEY}= names the "
            f"journal the leg belongs to (letters, digits, `-`, `_`, "
            f"`.`, `:`), on a type={LISTING_JOURNAL_TYPE} line only: "
            f"{shown!r}")
    if pair is not None:
        out['journal_pair'] = pair
    return out


def _check_futures_settlement(action: str, tx: dict, keys: dict,
                              line: str, source: str) -> dict:
    """The `type=futures_settlement [settle=LEG,MONEY,PL,CUR[,RATE]]`
    tokens of a BUYSELL line, checked: a part of a futures book on the
    settlement basis (lib/futures), its leg agreeing with the line's
    direction (LO and SC buy, LC and SO sell). Returns the fields they
    set on the row (backlog TJ-0662)."""
    from taxjson.lib.futures import is_plain_future
    where = _where(source)
    shown = line.strip()
    form = (f"{FUTURES_SETTLEMENT_TOKEN} [{SETTLE_KEY}=LEG,MONEY,PL,CUR"
            f"[,RATE]]")
    bad = sorted(set(keys) - {'type', SETTLE_KEY})
    if bad:
        raise ValueError(
            f"{where}unknown .tt token {bad[0]}={keys[bad[0]]} — a "
            f"settled futures line takes {form}: {shown!r}")
    sym = str(tx.get('symbol') or '')
    if action != 'BUYSELL' or not is_plain_future(sym):
        raise ValueError(
            f"{where}{FUTURES_SETTLEMENT_TOKEN} marks a futures contract's "
            f"part on the settlement basis: only a BUYSELL line of a "
            f"future (an F: or / symbol) takes it: {shown!r}")
    out = {'type': FUTURES_SETTLEMENT_TYPE, 'settle_native': ''}
    if SETTLE_KEY not in keys:
        return out
    native, leg = settle_native_of_tt(keys[SETTLE_KEY])
    if leg is None:
        raise ValueError(
            f"{where}{SETTLE_KEY}={keys[SETTLE_KEY]} is not a settlement's "
            f"native facts (LEG,MONEY,PL,CUR[,RATE], LEG one of LO LC SO "
            f"SC): {shown!r}")
    q = float(tx.get('quantity') or 0.0)
    if q == 0 or (q > 0) != (leg.leg in ('LO', 'SC')):
        raise ValueError(
            f"{where}{SETTLE_KEY}={keys[SETTLE_KEY]}: a {leg.leg} leg "
            f"{'buys' if leg.leg in ('LO', 'SC') else 'sells'}, the line's "
            f"QTY {tx.get('quantity')} does not: {shown!r}")
    if (leg.leg in ('LO', 'SC') and leg.money < 0
            and (tx.get('price') or 0.0) > 0):
        # A buy's money is its cost: negative only at a negative price.
        # An older taxjson-convert-tt wrote a cost in the cash sign as a
        # credit here, and the book's closes carry the gain it made of
        # it (backlog TJ-5162): never booked.
        raise ValueError(
            f"{where}{SETTLE_KEY}={keys[SETTLE_KEY]}: a {leg.leg} leg "
            f"buys, and its money {leg.money} is negative at a price "
            f"above 0: this settled futures line was written by an "
            f"older taxjson-convert-tt from a buy whose total was in the "
            f"cash sign, and the closes of that contract in this file "
            f"carry the wrong gain. Convert the source book again (your "
            f"export or .tt file, through `tjs run`) and replace this "
            f"file: {shown!r}")
    out['settle_native'] = native
    return out


def _check_corp(action: str, corp: dict, line: str, source: str,
                tx: dict) -> dict:
    """Validate a line's `corp=` / `election=` / `parent_qty=` tokens and
    its cash tokens (_CASH_KEYS: _check_cash_keys, which asks whether
    the line has a `corp=` and the type of `tx`, the row read so far)
    and its `settled=` date (on or after the line's date: the row's
    `date_settle`) or `traded=` date (_check_traded: the row's `date`),
    and map them to their row fields."""
    cash_keys = {k: corp.pop(k) for k in _CASH_KEYS if k in corp}
    settled = corp.pop(SETTLED_KEY, None)
    traded = corp.pop(TRADED_KEY, None)
    out = _check_cash_keys(action, cash_keys, 'corp' in corp,
                           str(tx.get('type') or ''), line, source,
                           'cash' in corp)
    shown = line.strip()
    if settled is not None:
        _check_date(settled, f'{SETTLED_KEY}=', line, source)
        if settled < str(tx.get('date') or ''):
            raise ValueError(
                f"{_where(source)}{SETTLED_KEY}={settled} is before the "
                f"line's date {tx.get('date')}: a row settles on or after "
                f"the day it trades or is paid (write the trade or pay "
                f"date first, its settlement date in {SETTLED_KEY}=): "
                f"{shown!r}")
        _check_date_gap(SETTLED_KEY, settled, tx, line, source)
        out['date_settle'] = settled
    if traded is not None:
        out['date'] = _check_traded(action, traded, settled, tx, line,
                                    source)
    for key, val in corp.items():
        if key == 'election' and not re.match(r'^[a-z0-9_]+$', val):
            raise ValueError(
                f"{_where(source)}election={val} must be an election's "
                f"name (rollover_s_86_1): {shown!r}")
        if key != 'corp' and 'corp' not in corp:
            raise ValueError(
                f"{_where(source)}{key}={val} needs the event it belongs "
                f"to (corp=<event id>): {shown!r}")
        if key == 'parent_qty':
            try:
                num = float(val)
            except ValueError:
                num = 0.0
            if action != 'ADJUST' or not math.isfinite(num) or num <= 0:
                raise ValueError(
                    f"{_where(source)}parent_qty={val}: an ADJUST line's "
                    f"count of parent shares, above 0: {shown!r}")
            val = num
        if key == 'cash':
            try:
                val = corp_cash_of_tt(val)
            except EvidenceError:
                raise ValueError(
                    f"{_where(source)}cash={val}: the cash the event paid, "
                    f"<amount>:<CUR> (7.5:USD), a later payment "
                    f"<amount>:<CUR>:@<date>, joined by `;`, each amount a "
                    f"number written out (7.5, 1250.00): {shown!r}") from None
        out[_CORP_KEYS[key]] = val
    return out


def _check_date_gap(key: str, val: str, tx: dict, line: str,
                    source: str) -> None:
    """Refuse a `settled=` / `traded=` date more than MAX_DATE_GAP_DAYS
    from the line's date: a typo'd year, not a settlement."""
    days = abs((datetime.strptime(val, '%Y-%m-%d')
                - datetime.strptime(tx['date'], '%Y-%m-%d')).days)
    if days > MAX_DATE_GAP_DAYS:
        raise ValueError(
            f"{_where(source)}{key}={val} is {days} days from the line's "
            f"date {tx['date']}: more than {MAX_DATE_GAP_DAYS} days is "
            f"read as a typo (check the year), never a settlement: "
            f"{line.strip()!r}")


def _check_traded(action: str, traded: str, settled: Optional[str],
                  tx: dict, line: str, source: str) -> str:
    """A line's `traded=` date, checked: a date, on or before the line's
    date (which is then the settlement date) and within
    MAX_DATE_GAP_DAYS of it, on a line with no `settled=`, of a kind
    whose line can show a settlement date (never an income, tax,
    interest or fee line: PAID_DATE_ACTIONS show the day paid)."""
    shown = line.strip()
    if settled is not None:
        raise ValueError(
            f"{_where(source)}{TRADED_KEY}={traded} and {SETTLED_KEY}="
            f"{settled} on one line: the line's date is the trade (or "
            f"pay) date with {SETTLED_KEY}=, or the settlement date with "
            f"{TRADED_KEY}= — keep one: {shown!r}")
    if action in PAID_DATE_ACTIONS:
        raise ValueError(
            f"{_where(source)}{TRADED_KEY}={traded}: {_a(action)} line's "
            f"date is the day it was paid, never a settlement date: it "
            f"takes {SETTLED_KEY}=<date> (when the cash settled later), "
            f"not {TRADED_KEY}=: {shown!r}")
    _check_date(traded, f'{TRADED_KEY}=', line, source)
    if traded > str(tx.get('date') or ''):
        raise ValueError(
            f"{_where(source)}{TRADED_KEY}={traded} is after the line's "
            f"date {tx.get('date')}: a row trades on or before the day it "
            f"settles (write the settlement date first, its trade date "
            f"in {TRADED_KEY}=): {shown!r}")
    _check_date_gap(TRADED_KEY, traded, tx, line, source)
    return traded


def _corp_tokens(tx: dict, action: str) -> str:
    """A row's corporate-action provenance tokens (_CORP_KEYS)."""
    if (action not in _CORP_ACTIONS + ('SPLIT',)
            or not tx.get('corp_event_id')):
        return ""
    toks = []
    for key, field in _CORP_KEYS.items():
        if action == 'SPLIT' and key not in _SPLIT_CORP_KEYS:
            continue
        val = tx.get(field)
        if key == 'parent_qty':
            val = _num(float(val)) if val and action == 'ADJUST' else ''
        elif key == 'cash':
            val = corp_cash_tt(val)
        val = str(val or '').strip()
        if val and not re.search(r'[\s#]', val):
            toks.append(f"{key}={val}")
    return (" " + " ".join(toks)) if toks else ""


def _a(action: str) -> str:
    """`an ADJUST`, `a FEE`: an action with its article."""
    return f"{'an' if action[:1] in 'AEIO' else 'a'} {action}"


def _cash_leg_problem(action: str, kind: str, has_corp: bool,
                      row_type: str, has_cash: bool = False) -> str:
    """Why a line of `action` does not take `cash_leg=<kind>` ('' when
    it does): the one rule of the reader and the writer. `has_cash`: the
    line has a `cash=` (the event's cash, which the ledgers read alone:
    a `cash_leg=cash` beside it would say the TOTAL moved too, and is
    read by nothing)."""
    if kind not in CASH_LEG_KINDS:
        return (f"one of {CASH_LEG_NONE} (the amount is a value: no cash "
                f"moved), {CASH_LEG_REWARD} (a reward paid in the line's "
                f"SYMBOL), {CASH_LEG_CASH} (the amount was paid in cash)")
    if kind == CASH_LEG_NONE and action not in _CASH_LEG_NONE_ACTIONS:
        return (f"only a trade or an income line "
                f"({', '.join(_CASH_LEG_NONE_ACTIONS)}) takes it: "
                f"{_a(action)} line moves no cash without it")
    if kind == CASH_LEG_REWARD and action not in _CASH_LEG_REWARD_ACTIONS:
        return ("a reward is its DIVIDEND line (the income) and its "
                "BUYSELL line (the coins acquired): only those take it")
    if kind == CASH_LEG_CASH:
        if action == 'ADJUST':
            if row_type != 'roc':
                return ("on an ADJUST line it marks a return of capital "
                        "paid in cash: the line needs type=roc too")
        elif not has_corp:
            return (f"{_a(action)} line's amount is cash already: only an "
                    f"ADJUST type=roc line, or a line with corp=<event "
                    f"id> (the event's own cash leg), takes it")
        if has_cash:
            return ("the line has cash= too, and the cash a corp= line "
                    "moved is its cash= alone: keep cash= (the event's "
                    "cash), or cash_leg=cash (the line's own amount)")
    return ''


def _check_cash_keys(action: str, keys: dict, has_corp: bool,
                     row_type: str, line: str, source: str,
                     has_cash: bool = False) -> dict:
    """Validate a line's `cash_leg=` / `refunds=` tokens and map them to
    their row fields (_CASH_KEYS; neither is part of the row's id)."""
    out = {}
    shown = line.strip()
    if CASH_LEG_KEY in keys:
        kind = keys[CASH_LEG_KEY]
        why = _cash_leg_problem(action, kind, has_corp, row_type, has_cash)
        if why:
            raise ValueError(
                f"{_where(source)}{CASH_LEG_KEY}={kind}: {why}: {shown!r}")
        out[_CASH_KEYS[CASH_LEG_KEY]] = kind
    if REFUNDS_KEY in keys:
        val = keys[REFUNDS_KEY]
        if action not in _REFUND_ACTIONS:
            raise ValueError(
                f"{_where(source)}{REFUNDS_KEY}={val}: the commission "
                f"refunds a trade's total is net of: only a BUYSELL or an "
                f"ASSIGN line takes it: {shown!r}")
        try:
            out[_CASH_KEYS[REFUNDS_KEY]] = commission_refunds_of_tt(val)
        except EvidenceError:
            raise ValueError(
                f"{_where(source)}{REFUNDS_KEY}={val}: the commission "
                f"refunds the line's total is net of, <date>:<amount> "
                f"(2025-02-14:1.00), joined by `;`, each date YYYY-MM-DD "
                f"and each amount a number other than 0 written out: "
                f"{shown!r}") from None
    return out


def _cash_tokens(tx: dict, action: str) -> str:
    """A row's cash tokens (_CASH_KEYS): `cash_leg=` as lib/cash_legs
    `cash_leg_of` reads the row (left out where the line does not take
    it: json_to_tt_lines then refuses the row), `refunds=` on a trade."""
    if action not in _CORP_ACTIONS:
        return ""
    from taxjson.lib.cash_legs import cash_leg_of
    toks = []
    kind = cash_leg_of(tx)
    if kind and not _cash_leg_problem(
            action, kind, bool(tx.get('corp_event_id')),
            str(tx.get('type') or '').lower(),
            bool(corp_cash_tt(tx.get('corp_cash')))):
        toks.append(f"{CASH_LEG_KEY}={kind}")
    if action in _REFUND_ACTIONS:
        refunds = commission_refunds_tt(tx.get('commission_refunds'))
        if refunds:
            toks.append(f"{REFUNDS_KEY}={refunds}")
    return (" " + " ".join(toks)) if toks else ""


def _check_facts(action: str, facts: dict, line: str, source: str,
                 has_corp: bool = False) -> dict:
    """Validate the `key=value` income-fact tokens of one line and map
    them to their row fields. `has_corp`: the line has a `corp=` (an
    ADJUST line's `type=spinoff_355` needs its event)."""
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
            if (action == 'ADJUST' and val == SPINOFF_355_TYPE
                    and not has_corp):
                raise ValueError(
                    f"{_where(source)}type={val} marks the parent's basis "
                    f"a section 355 spin-off moved to the new shares: the "
                    f"line needs the event it belongs to (corp=<event "
                    f"id>, as taxjson-convert-tt writes it): "
                    f"{line.strip()!r}")
            if action != 'ADJUST' or val not in _ADJUST_TYPES + (
                    SPINOFF_355_TYPE,):
                raise ValueError(
                    f"{_where(source)}type={val}: only an ADJUST line "
                    f"takes a type ({', '.join(_ADJUST_TYPES)}; "
                    f"{SPINOFF_355_TYPE} with corp=<event id>): "
                    f"{line.strip()!r}")
        elif key == 'label':
            if not re.match(r'^[a-z_]+$', val):
                raise ValueError(
                    f"{_where(source)}label={val} must be a lower-case "
                    f"word (distribution): {line.strip()!r}")
        out[field] = val
    return out


def tt_tail(tx: dict, action: str) -> str:
    """The optional tokens at the end of a row's .tt line, as BOTH .tt
    writers append them (tx_to_tt_line and the `tjs events` view,
    cli/listings._tx_display_line): a trade's contract size and warrant
    exercise (`x<size>`, `exercise_of=`), an income row's facts
    (`record=`, `label=` ...), the cash facts (`cash_leg=`, `refunds=`).
    The view wrote none of the facts, so a
    pasted trust distribution moved to its pay year (backlog TJ-0390).
    '' when there are none; else the tokens with a leading space."""
    if action in ('BUYSELL', 'ASSIGN'):
        return (_trade_tokens(tx, action) + _corp_tokens(tx, action)
                + _cash_tokens(tx, action))
    if action == 'INTEREST':
        return _interest_tokens(tx)
    if action == 'SPLIT':
        return _corp_tokens(tx, action)
    if action == 'TRANSFER':
        return _transfer_tokens(tx)
    return (_fact_tokens(tx, action) + _corp_tokens(tx, action)
            + _cash_tokens(tx, action))


def _interest_tokens(tx: dict) -> str:
    """An INTEREST line's `symbol=` token: the property the interest
    is paid on (backlog TJ-0856); none for interest on the account's
    cash (`CASH`, or no symbol)."""
    sym = str(tx.get('symbol') or '').strip()
    if not sym or sym.upper() == 'CASH' or any(c.isspace() for c in sym):
        return ""
    return f" {INTEREST_SYMBOL_KEY}={sym}"


def size_text(size: float) -> str:
    """A contract size as the `x<size>` token writes it: the shortest
    exact fixed-point decimal (`1000000`, `0.00001`, `12.5`). `:g`
    wrote `1e+06` and `1e-05`, which the reader refused (backlog
    TJ-0385)."""
    from decimal import Decimal
    s = format(Decimal(repr(float(size))), 'f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s


def _opening_size_token(tx: dict) -> str:
    """An OPENING line's ` x<size>`: an option's declared contract size
    other than 100 (backlog TJ-0963); '' otherwise."""
    from taxjson.lib.option_symbols import is_option_symbol
    try:
        size = float(tx.get('multiplier') or 0.0)
    except (TypeError, ValueError):
        return ''
    if (math.isfinite(size) and size > 0 and size != 100.0
            and is_option_symbol(str(tx.get('symbol') or ''))):
        return f" x{size_text(size)}"
    return ''


def _trade_tokens(tx: dict, action: str) -> str:
    """A BUYSELL / ASSIGN line's optional tokens after the fee."""
    from taxjson.lib.option_symbols import is_option_symbol
    symbol = str(tx.get('symbol') or '')
    try:
        size = float(tx.get('multiplier') or 0.0)
    except (TypeError, ValueError):
        size = 0.0
    if not math.isfinite(size):
        size = 0.0
    exercise = (str(tx.get(EXERCISE_KEY) or '').strip()
                if action == 'ASSIGN' else '')
    if ' ' in exercise:
        exercise = ''
    toks = []
    # A declared contract size other than the equity option's 100 (or
    # any size on a futures line) rides along as `x<size>` (audit
    # S026-22), and a warrant's declared shares per warrant on its
    # exercise leg (engine/assign._assign_delivery_shares).
    if size > 0 and (symbol.startswith(FUTURES_PREFIXES)
                     or (is_option_symbol(symbol) and size != 100.0)
                     or exercise):
        toks.append(f"x{size_text(size)}")
    if exercise:
        toks.append(f"{EXERCISE_KEY}={exercise}")
    from taxjson.lib.futures import is_plain_future, is_settlement_row
    if (action == 'BUYSELL' and is_settlement_row(tx)
            and is_plain_future(symbol)):
        toks.append(FUTURES_SETTLEMENT_TOKEN)
        native = settle_native_tt(tx.get('settle_native'))
        if native:
            toks.append(f"{SETTLE_KEY}={native}")
    from taxjson.lib.trade_cancel import is_trade_cancel
    if is_trade_cancel(tx):
        toks.append(TRADE_CANCEL_TOKEN)
    from taxjson.lib.row_types import is_stock_dividend
    typ = str(tx.get('type') or '')
    run_type = action == 'BUYSELL' and typ in RUN_TRADE_TYPES
    if action == 'BUYSELL' and is_stock_dividend(tx):
        toks.append(STOCK_DIVIDEND_TOKEN)
    elif run_type:
        toks.append(f"type={typ}")
    if (STOCK_DIVIDEND_TOKEN in toks
            or (run_type and typ == SPINOFF_355_TYPE)):
        for key, field in _STOCKDIV_DATE_KEYS.items():
            val = str(tx.get(field) or '').strip()
            if _is_date(val):
                toks.append(f"{key}={val}")
    if STOCK_DIVIDEND_TOKEN in toks and tx.get('delivered_cost'):
        toks.append(f"{DELIVERED_KEY}={_num(tx['delivered_cost'])}")
    if run_type and typ == REORG_356_TYPE and tx.get('gross_amount'):
        # The boot the engine reads from the sale's `gross_amount`.
        toks.append(f"{BOOT_KEY}={_num(tx['gross_amount'])}")
    return (" " + " ".join(toks)) if toks else ""


def _transfer_tokens(tx: dict) -> str:
    """A TRANSFER line's `type=` token and a listing journal's `pair=`
    (RUN_TRANSFER_TYPES); '' for any other row. A journal leg whose
    `journal_pair` is not one word gets none (json_to_tt_lines then
    refuses the row: _unwritable_type)."""
    typ = str(tx.get('type') or '')
    if typ not in RUN_TRANSFER_TYPES:
        return ""
    pair = str(tx.get('journal_pair') or '')
    if typ == LISTING_JOURNAL_TYPE:
        return (f" type={typ} {PAIR_KEY}={pair}" if _PAIR_RE.match(pair)
                else "")
    return f" type={typ}"


def _fact_tokens(tx: dict, action: str) -> str:
    """The `key=value` tokens that carry a row's income facts (only the
    actions whose lines take them: a DISALLOW line read none back)."""
    if action not in _FACT_ACTIONS:
        return ""
    toks = []
    for key, field in _FACT_KEYS.items():
        val = str(tx.get(field) or '').strip()
        if not val:
            continue
        if key == 'type':
            if action != 'ADJUST' or not (
                    val.lower() in _ADJUST_TYPES
                    or (val == SPINOFF_355_TYPE
                        and tx.get('corp_event_id'))):
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


def _tt_contract_size(tx: dict) -> float:
    """The units one contract of a .tt BUYSELL / ASSIGN line covers,
    for the total check: an equity option's ticker.map `MULT` line (the
    option, its root, or its underlying; the one `tjs run` reads,
    lib/markets.contract_size) first, as the engine sizes it (core.
    equity_option_size); else the line's `x<size>`; else 100 for an
    equity option and 1 for anything else. Without the MULT line a
    mini option's correct total read as a typo at x100, which `run
    --strict` stops on (backlog TJ-0864)."""
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_underlying
    sym = str(tx.get('symbol') or '')
    option = is_option_symbol(sym) and not sym.startswith(FUTURES_PREFIXES)
    if option:
        from taxjson.lib import markets
        und = parse_option_underlying(sym) or ''
        ovr = markets.option_contract_size(sym, und)
        if ovr:
            return float(ovr)
    if tx.get('multiplier'):
        return float(tx['multiplier'])
    return 100.0 if option else 1.0


def _adjusted_series_total(tx: dict, total: float) -> bool:
    """Whether `total` on an option line is qty x price x 100 +/- fee under
    a ticker.map MULT line of an adjusted series: MULT sizes the
    deliverable and the premium stays x100, the generic importer's rule
    (backlog TJ-1034, TJ-1147). A mini's 10 is the premium's size too: its
    x100 total stays a typo (TJ-0864)."""
    from taxjson.lib import markets
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_underlying
    sym = str(tx.get('symbol') or '')
    if not is_option_symbol(sym) or sym.startswith(FUTURES_PREFIXES):
        return False
    size = markets.option_contract_size(
        sym, parse_option_underlying(sym) or '')
    if not size or float(size) in (10.0, 100.0):
        return False
    q = tx['quantity']
    fee = tx.get('fee', 0.0)
    at_100 = abs(q) * tx['price'] * 100.0 + (fee if q > 0 else -fee)
    return abs(total - at_100) <= tolerance(at_100)


def _check_total_sign(action: str, tx: dict, parts: list, typed: bool,
                      line: str, source: str) -> None:
    """The sign of an unsettled BUYSELL / ASSIGN line's TOTAL, refused
    where it cannot be read as one trade. `parts`: the line's tokens
    (the price and the total as written are quoted). `typed`: the line
    carries a `type=` token (a cancellation, a stock dividend), whose
    futures total is not read against the price."""
    written = parts[7]
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
    # BUY total at a price above 0 cannot mean anything but the cash
    # sign (qty x price + fee is never negative): the row keeps it as
    # typed, and the engines and the futures settlement take a buy's
    # cost as its magnitude (engine _ca_trade_money, lib/futures
    # _money). A future's price can be negative, where a buy receives
    # money and a sale pays: _check_futures_total reads the total
    # against the line (backlog TJ-5162). No other security trades
    # below 0, so a negative price on one is refused here, at its line
    # (_refuse_negative_price): the total checks above the call stand
    # down at a price below 0, and such a line reached the books unread
    # (backlog TJ-5031). A future's ASSIGN line is refused whatever its
    # price (_refuse_futures_assign): the settlement books BUYSELL
    # fills only, and stopped the run on it, naming no line.
    _refuse_negative_price(action, tx, parts[6], line, source)
    _refuse_futures_assign(action, tx, line, source)
    from taxjson.lib.futures import is_plain_future
    below_zero = ((tx.get('price') or 0.0) < 0
                  and is_plain_future(str(tx.get('symbol') or '')))
    if action == 'BUYSELL' and not typed:
        _check_futures_total(tx, parts[6], written, line, source)
    if tx.get('quantity', 0) < 0 and tx.get('net_amount', 0) < 0 \
            and below_zero:
        # A sale at a negative price pays: its negative total is the
        # notional, not a commission larger than the gross. Proved
        # against the line above; a typed line (a cancellation of a buy
        # at a negative price), whose total is otherwise not read
        # against the price, proves a negative one here, so what is
        # refused is refused for what it is.
        if typed:
            _check_futures_total(tx, parts[6], written, line, source)
        return
    if (tx.get('quantity', 0) < 0 and tx.get('net_amount', 0) < 0
            and not _excess_commission_sale(tx)):
        raise ValueError(
            f"{_where(source)}{action} sell total {written} is negative "
            f"— a .tt total is the net proceeds (qty x price - "
            f"commission); the sell direction lives in the negative qty. "
            f"If you typed the cash sign, drop the '-'. A negative total "
            f"is read only when the line's commission exceeds qty x "
            f"price{' (add the contract size, e.g. x1000)' if str(tx.get('symbol') or '').startswith(FUTURES_PREFIXES) and not tx.get('multiplier') else ''} "
            f"and the total equals qty x price - commission. "
            f"Line: {line.strip()!r}")


# A price this near 0 is 0: float noise of a computed price (-1e-13) is
# not a negative price. One figure for the reader and the run's row
# check (lib/brokerages/schema `_negative_price`; backlog TJ-5261).
PRICE_ZERO = 1e-9


def _refuse_negative_price(action: str, tx: dict, price_text: str,
                           line: str, source: str) -> None:
    """A BUYSELL / ASSIGN line whose price is below 0 and whose symbol
    is not a futures contract, refused naming the line: a share's price
    and an option's premium (an option on a future too) are never
    negative, so nothing says what the line's total means, and every
    total check stands down at such a price. The run's row check refused
    a share's or an equity option's later, naming no line, and let an
    option on a future through (backlog TJ-5031)."""
    from taxjson.lib.futures import is_plain_future
    if not (tx.get('price') or 0.0) < -PRICE_ZERO \
            or is_plain_future(str(tx.get('symbol') or '')):
        return
    raise ValueError(
        f"{_where(source)}{action} price {price_text} is negative: only "
        f"a futures contract (an F: or / symbol, not an option on one) "
        f"trades below 0; a share's price and an option's premium never "
        f"do. Write the price above 0 (the direction is the QTY's sign, "
        f"and the total is the net cash). Line: {' '.join(line.split())!r}")


def _refuse_negative_transfer_price(tx: dict, price_text: str, line: str,
                                    source: str) -> None:
    """A TRANSFER line whose price is below 0, refused naming the line,
    as an INKIND line's negative value and an OPENING line's negative
    cost are: the price of a transfer is the per-unit value of what
    moved, never below 0, and no check read it (the total checks are a
    trade line's; the run's row check reads a trade's price only), so
    the line reached the books as written (backlog TJ-5262). A plain
    future's line reads as its BUYSELL line does: a future trades below
    0."""
    from taxjson.lib.futures import is_plain_future
    if not (tx.get('price') or 0.0) < -PRICE_ZERO \
            or is_plain_future(str(tx.get('symbol') or '')):
        return
    raise ValueError(
        f"{_where(source)}TRANSFER price {price_text} is negative: a "
        f"transfer line's price is the value of one unit moved, never "
        f"below 0 (the direction is the QTY's sign: negative = out). "
        f"Write the price above 0, or 0 when the value is unknown. "
        f"Line: {' '.join(line.split())!r}")


def _refuse_futures_assign(action: str, tx: dict, line: str,
                           source: str) -> None:
    """An ASSIGN line of a futures contract (not an option on one) that
    moves units, refused naming the line with the settlement's own reason
    (lib/futures settle_futures: only BUYSELL fills of a futures contract
    can be booked): the reader took the line, at any price, and the run
    stopped at the settlement, which names the row by its id and no file
    or line (the review of TJ-5031). The future an option on it delivers
    is written as the BUYSELL fill it is."""
    from taxjson.lib.futures import is_plain_future
    sym = str(tx.get('symbol') or '')
    if action != 'ASSIGN' or not is_plain_future(sym) \
            or not abs(tx.get('quantity') or 0.0) > 0:
        return
    raise ValueError(
        f"{_where(source)}ASSIGN line of the futures contract {sym}: only "
        f"BUYSELL fills of a futures contract can be booked (on their "
        f"settled P/L). Write the line as BUYSELL (the fill the exercise "
        f"or assignment gave, at its price, with the contract size after "
        f"the fee, e.g. x1000), or remove it. Line: "
        f"{' '.join(line.split())!r}")


def _check_futures_total(tx: dict, price_text: str, written: str,
                         line: str, source: str) -> None:
    """A plain-futures fill's TOTAL against the line's own price: the
    refusal of a total that cannot be read as one trade (backlog
    TJ-5162). What is read, and booked alike in every figure:

    * price above 0: a BUY total is the cost, written as the cost or
      as the cash paid out (either sign: the magnitude is booked, as a
      share line's is); a SELL total is the proceeds (the rule every
      .tt sale follows, checked by the caller);
    * price below 0 (a crude oil contract settled below zero in 2020):
      a buy RECEIVES money and a sale PAYS it, so the total carries the
      sign of qty x price x size + fee (a buy), - fee (a sale): a
      negative total, or a buy's positive cost when its commission is
      larger than the credit. That amount is booked signed, so it is
      read only where the line proves it: the size on the line
      (`x1000`), the total within the typo tolerance of it and of its
      sign (the tolerance's 0.05 floor is for the size: +0.02 for -0.02
      is the opposite trade). Anything else (a total of the other sign,
      the magnitude alone, no size) is refused;
    * no price (0): a negative BUY total is refused: nothing on the
      line says whether it is a cost in the cash sign or the credit of
      a negative price, and the two are opposite amounts.
    """
    from taxjson.lib.futures import is_plain_future
    if not is_plain_future(str(tx.get('symbol') or '')):
        return
    # (the reader's own floats: _tt_num)
    q = tx.get('quantity') or 0.0
    price = tx.get('price') or 0.0
    net = tx.get('net_amount') or 0.0
    if not q or price > 0:
        return
    shown = ' '.join(line.split())
    if price == 0:
        if q > 0 and net < 0:
            raise ValueError(
                f"{_where(source)}futures buy total {written} is negative "
                f"and the line has no price (0): it reads as a cost "
                f"written with the cash sign, or as the money a buy at a "
                f"negative price receives, and the two are opposite "
                f"amounts. Write the total as the cost (drop the '-'), "
                f"or write the contract's negative price and its size "
                f"(e.g. x1000). Line: {shown!r}")
        return
    side = 'buy' if q > 0 else 'sell'
    moves = 'receives' if q > 0 else 'pays'
    head = (f"{_where(source)}futures {side} at the negative price "
            f"{price_text}: a {side} at a negative price {moves} money, so "
            f"its total is qty x price x size {'+' if q > 0 else '-'} fee "
            f"with its sign")
    if not tx.get('multiplier'):
        raise ValueError(
            f"{head}. The line has no contract size to read the total "
            f"{written} against: add it after the fee (e.g. x1000). If "
            f"the '-' of the price is a typo, write the price above 0. "
            f"Line: {shown!r}")
    fee = tx.get('fee') or 0.0
    expected = (abs(q) * price * _tt_contract_size(tx)
                + (fee if q > 0 else -fee))
    if (abs(net - expected) > tolerance(expected)
            or (expected != 0 and (net < 0) != (expected < 0))):
        raise ValueError(
            f"{head}, {M.shown(expected, 2):.2f} here; the line's total "
            f"is {written}. Write that total (the other sign is the "
            f"opposite trade), or the price above 0 if its '-' is a "
            f"typo. Line: {shown!r}")


def _excess_commission_sale(tx: dict) -> bool:
    """A SELL (or ASSIGN) line whose negative total is a commission
    larger than its gross: fee > |qty| x price x size and the total is
    |qty| x price x size - fee within the typo-check tolerance. A
    futures line needs its size on the line (`x1000`) to tell. Never a
    line at a price below 0: there the gross itself is negative (a
    future's sale pays its notional, `_check_futures_total`), which is
    not a commission (backlog TJ-5031)."""
    sym = str(tx.get('symbol') or '')
    is_fut = sym.startswith(FUTURES_PREFIXES)
    if is_fut and not tx.get('multiplier'):
        return False
    mult = _tt_contract_size(tx)
    gross = abs(float(tx.get('quantity') or 0.0)) * float(
        tx.get('price') or 0.0) * mult
    fee = float(tx.get('fee') or 0.0)
    expected = gross - fee
    net = float(tx.get('net_amount') or 0.0)
    if gross < 0:
        return False
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
    from taxjson.lib.option_symbols import is_option_symbol
    for key in ('symbol', 'symbol_new', EXERCISE_KEY):
        sym = tx.get(key) or ''
        if ('.' not in sym or sym == 'CASH' or is_option_symbol(sym)
                or sym.startswith(FUTURES_PREFIXES)):
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
    # `TaxTransaction.compute_id` (`model/transaction.py`). The earlier
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
    components += event_identity(tx.get('symbol', ''), tx.get('symbol_new'),
                                 tx.get('corp_event_id'), tx.get('action', ''))
    raw_id = "|".join(str(c) for c in components)
    return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]


def tx_tt_date(tx: dict, date_basis: str = 'settle') -> str:
    """The one date a row's .tt line carries: on the settle basis a
    trade's settlement date, on the trade basis its trade date; an
    income, withholding or fee row's (PAID_DATE_ACTIONS) the day it was
    paid on either basis (backlog TJ-0862)."""
    action = str(tx.get('action') or '').upper()
    if (date_basis == 'settle' and tx.get('date_settle')
            and action not in PAID_DATE_ACTIONS):
        return tx['date_settle']
    return tx.get('date', '')


# The clock a .tt line gives a row with none (the reader requires the
# column; TaxTransaction's own default).
UNKNOWN_TT_TIME = '09:30:00'
_CLOCK_RE = re.compile(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?$')


def tt_time(tx: dict) -> str:
    """The time column of a row's .tt line: UNKNOWN_TT_TIME for a row
    without one (no key, null or empty: the JSON loaders accept all
    three), a clock the loaders accept unpadded (`9:30`) written
    HH:MM:SS. An empty or null time wrote a missing column or `None`,
    a line the reader refused (backlog TJ-0983)."""
    raw = tx.get('time')
    text = '' if raw is None else str(raw).strip()
    if not text:
        return UNKNOWN_TT_TIME
    m = _CLOCK_RE.match(text)
    if m:
        return (f"{int(m.group(1)):02d}:{m.group(2)}:"
                f"{m.group(3) or '00'}")
    return text


def date_token(tx: dict, date_basis: str = 'settle') -> str:
    """A row's second date as a token, when its line's date (tx_tt_date)
    is not both: ` settled=<date>`, the settlement date after a line's
    trade or pay date (an income row; a trade on the trade basis), or
    ` traded=<date>`, the trade date before a line's settlement date (a
    trade on the settle basis). '' when the row has one date, for an
    OPENING line (no time column, one day), and for a row no line
    says: one that settles before it trades or is paid, or more than
    MAX_DATE_GAP_DAYS after (json_to_tt_lines refuses it:
    _unwritable_cash)."""
    from taxjson.lib.row_types import is_opening_row
    shown = str(tx_tt_date(tx, date_basis) or '')
    td = str(tx.get('date') or '').strip()
    sd = str(tx.get('date_settle') or '').strip()
    if (not _is_date(shown) or not _is_date(td) or not _is_date(sd)
            or td >= sd or is_opening_row(tx)
            or tx.get('action') not in _VALID_ACTIONS
            or (datetime.strptime(sd, '%Y-%m-%d')
                - datetime.strptime(td, '%Y-%m-%d')).days
            > MAX_DATE_GAP_DAYS):
        return ''
    return (f" {SETTLED_KEY}={sd}" if shown == td
            else f" {TRADED_KEY}={td}")


def tx_to_tt_line(tx: dict, date_basis: str = 'settle'):
    """A row's .tt line (_row_line) with its `settled=` date
    or `traded=` date (date_token); None for a row with no .tt line."""
    line = _row_line(tx, date_basis)
    return line if line is None else line + date_token(tx, date_basis)


def _row_line(tx: dict, date_basis: str = 'settle'):
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
    trade-basis project). An income, withholding or fee row keeps the
    day it was paid on either basis (tx_tt_date; backlog TJ-0862)."""
    action = tx.get('action', '')
    if action not in _VALID_ACTIONS:
        return None
    if 'qty' in tx and 'quantity' not in tx:
        # The loaders' alias (tx_load.coerce_transaction_row): read as 0,
        # every trade's quantity was written 0 (backlog TJ-0977).
        tx = {**tx, 'quantity': tx['qty']}
    from taxjson.lib.row_types import is_opening_row
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
                f"{M.units(float(tx.get('net_amount') or 0.0), 5):.5f}"
                + (f" {lot}" if lot else '')
                + _opening_size_token(tx))

    date = tx_tt_date(tx, date_basis)
    time = tt_time(tx)
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
        # The contract size (`x<size>`) and a warrant exercise's
        # `exercise_of=` ride along (tt_tail).
        # A BUY's total is written as the row holds it, the cash sign
        # of a JSON book included (`net_amount` -200: -200.00000): the
        # reader, the engines and the futures settlement book either
        # sign as the one cost, and the row's id stays the one its
        # source line has, so a re-import beside it still dedupes
        # (backlog TJ-5162).
        return (f"{action} {date} {time} {symbol} {_num(qty)} {currency} "
                f"{_num(price)} {M.units(net, 5):.5f} {M.units(fee, 5):.5f}"
                + tt_tail(tx, action))

    if action == 'TRANSFER':
        line = (f"TRANSFER {date} {time} {symbol} {_num(qty)} {currency} "
                f"{_num(price)} {M.units(net, 5):.5f}")
        # Round-trip the opt-in declaration token: without it a
        # json→tt→json cycle would strip attestation from declared
        # rows (and could never re-grant it, by design).
        if tx.get('description') == MANUAL_TRANSFER_DECLARATION:
            line += " DECLARED"
        return line + tt_tail(tx, action)

    if action == 'SPLIT':
        # SPLIT date time symbol_old symbol_new ratio
        symbol_new = tx.get('symbol_new') or symbol
        return (f"SPLIT {date} {time} {symbol} {symbol_new} {_num(qty)}"
                + tt_tail(tx, action))

    if action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'):
        # ACTION date time symbol qty currency price total
        # SIGNED total: a broker reversal/refund row is negative and must
        # round-trip — abs() here re-inflated income/tax on json→tt→json
        # (the exact bug the sign-preserving IB parser fix removed), and the
        # changed net_amount hashed to a different id, breaking dedup
        # against the originals. The .tt parser reads the value signed.
        line = (f"{action} {date} {time} {symbol} {_num(qty)} {currency} "
                f"{_num(price)} {M.units(gross, 5):.5f}")
        # Optional 9th column: the withholding-NETTED amount, emitted
        # only when it differs from gross — without it a json→tt→json
        # cycle re-parsed net as gross and inflated income. Legacy
        # positional consumers ignore trailing columns.
        if abs(net - gross) > 0.005:
            line += f" {M.units(net, 5):.5f}"
        return line + tt_tail(tx, action)

    if action in ('INTEREST', 'FEE'):
        # ACTION date time currency amount  (sign preserved on interest)
        # [symbol=SYMBOL]: the property an INTEREST row is paid on.
        return (f"{action} {date} {time} {currency} {M.units(net, 5):.5f}"
                + tt_tail(tx, action))

    if action in ('ADJUST', 'DISALLOW'):
        # ACTION date time symbol currency amount
        return (f"{action} {date} {time} {symbol} {currency} {M.units(net, 5):.5f}"
                + tt_tail(tx, action))

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
    p = Path(input_path).resolve()
    for d in list(p.parents)[:3]:
        cfg = d / 'taxjson.toml'
        if cfg.is_file():
            try:
                doc = read_toml(cfg)
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
    from taxjson.lib.option_symbols import is_option_symbol
    for key in ('symbol', 'symbol_new'):
        sym = tx.get(key) or ''
        if (not sym or '.' in sym or sym == 'CASH' or is_option_symbol(sym)
                or sym.startswith(FUTURES_PREFIXES)):
            continue
        emit_line(f"warning: {_where(source)}symbol {sym} has no market suffix "
                  f"(e.g. {sym}.US, {sym}.TO) in an account that is not "
                  f"crypto = true — it is its own ACB pool, and the broker's "
                  f"rows for the real listing go short: {line.strip()!r}",
                  file=sys.stderr)


def tt_to_json(input_path: Path, account_name: str,
               equity: Optional[bool] = None) -> dict:
    """`equity`: the account is not `crypto = true` (tjs run says
    so: with exports shared by every year the .tt is not under the
    project's folder); None: read from the taxjson.toml above it."""
    from taxjson.lib.brokerages.base import shown_name, source_key
    transactions = []
    if equity is None:
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
        _last = strip_tt_comment(tt_lines(_text)[-1]).strip()
        if _last:
            emit_line(f"warning: {shown_name(input_path)}: the last line has no line "
                      f"end — if the file was cut short, its last number may "
                      f"be truncated; check it: {_last!r}", file=sys.stderr)
    with io.StringIO(_text) as f:
        for lineno, line in enumerate(f, 1):
            source = f"{shown_name(input_path)}:{lineno}"
            # An INKIND line values an in-kind move (`tjs run` reads
            # it, lib/in_kind): checked here, never a row of the books.
            if parse_inkind_line(line, source) is not None:
                continue
            # A dated JOURNAL / RENAME is an event `tjs run` books
            # (lib/dated_events): checked here, never a row of the file.
            if (parse_journal_line(line, source) is not None
                    or parse_rename_line(line, source) is not None):
                continue
            # Units held before the data, cost unknown (lib/
            # missing_history.read_tt_openings): read by `tjs run`,
            # checked here, never a row of the file.
            if parse_unknown_opening_line(line, source) is not None:
                continue
            # A filing position against the loss rule (lib/loss_overrides):
            # read by `tjs run`, never a row of the books.
            from taxjson.lib.loss_override_note import parse_line as _allowloss
            if _allowloss(line, source) is not None:
                continue
            # Not one move of your own (lib/transfer_in.NotOwn): read by
            # `tjs run`, never a row of the books.
            if parse_not_own_line(line, source) is not None:
                continue
            # A line of the FX-on-cash ledger v2 (lib/cash_lines: FXCONV,
            # CASHMOVE, CASHOPEN, CASHBAL, CASHBOOK): checked here, read by
            # `tjs fx-cash --ledger v2`, never a row of the books.
            from taxjson.lib.cash_lines import CashLineError, parse_line as _cashline
            try:
                if _cashline(line, source) is not None:
                    continue
            except CashLineError as e:
                # Still refused (a typo'd line must not vanish), but said
                # for what it is: under the default ledger nothing reads
                # it (pre-release review).
                raise CashLineError(
                    f"{e} — a line of the FX-on-cash ledger v2 only "
                    f"(read with fx_cash_ledger = \"v2\" or `tjs "
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
                    # The line the row came from (popped below into the
                    # file's `source_lines`).
                    tx['source_line'] = lineno
                    transactions.append(tx)
    # Per-file split-fill disambiguation, exactly as every brokerage
    # parser does: two byte-identical hand-entered lines (one order
    # filled in two pieces at the same price; the default 09:30:00
    # time gives no sub-day resolution) hashed to the same id and
    # collapsed to ONE trade under `tjs run`'s always-on --dedup
    # (stage-tools audit). The 2nd+ instance gets a `[fill #N]`
    # description marker and its id is recomputed from the marked
    # content; the first keeps its stable id. DECLARED counter-
    # transfers are exempt: the pipeline recognises the declaration by
    # EXACT description equality, and the ACQUIRED expansion relies on
    # a hand-written pair hashing identically.
    from taxjson.lib.brokerages.base import BaseBrokerage
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
    # {id: line}: the line each row came from, as a broker row's id and
    # file trace it — `tjs audit` names it (backlog TJ-0359). In the
    # metadata, not on the row: a reader that builds a TaxTransaction
    # from a converted row's keys keeps working.
    lines: dict = {}
    for tx in transactions:
        line = tx.pop('source_line', None)
        tx['id'] = compute_tt_id(tx)
        if line is not None:
            lines.setdefault(tx['id'], line)
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
            "source_lines": lines,
        },
    }


def _json_book_rows(input_path: Path) -> list:
    """A JSON book's rows for the .tt writer, read and checked as every
    tool reads a book (lib/tx_load: a BOM, `#` comments, each row's
    coercion and refusals), written from the rows as the file holds
    them. A trade row with no quantity or no net_amount is refused: its
    .tt line carried a quantity of 0 beside the row's total, or a total
    of 0, read back as a cost or proceeds of 0 (backlog TJ-5035);
    `"net_amount": 0` is a real amount and stays."""
    from taxjson.lib.cli_diag import InputContentError
    from taxjson.lib.tx_load import (coerce_transaction_row, fold_row_symbols,
                                     read_book_rows)
    try:
        rows = read_book_rows(input_path)
        checked = [coerce_transaction_row(t, i, str(input_path))
                   for i, t in enumerate(rows)]
    except json.JSONDecodeError:
        raise
    except ValueError as e:
        raise InputContentError(str(e)) from None
    missing = [m for m in (getattr(t, '_missing_trade_fields', None)
                           for t in checked) if m]
    if missing:
        ctx, fields = missing[0]
        wrote = {("quantity",): "a quantity of 0 beside the row's total",
                 ("net_amount",): "a total of 0, read as a cost or "
                                  "proceeds of 0"}.get(
            tuple(fields), "a quantity of 0 and a total of 0")
        raise InputContentError(
            f"{ctx}: required field(s) {', '.join(fields)} missing on a "
            f"trade row ({len(missing)} such row(s)): its .tt line would "
            f"carry {wrote} — nothing was written. Fix the input data.")
    # The symbols as the books spell them, as the coercion reads them (a
    # future's `/` or `\\` `F:`: backlog TJ-5150).
    return [fold_row_symbols(dict(t)) for t in rows]


def _row_named(input_path: Path, i: int, tx: dict) -> str:
    """A JSON book's row as a refusal names it: the book, the row's
    place and id, what it is, and the file its parser read it from."""
    src = str(tx.get('source') or '').strip()
    return (f"{input_path}: transaction at index {i} "
            f"(id={tx.get('id', '?')!r}, {tx.get('action', '?')} "
            f"{tx.get('date', '?')} {tx.get('symbol') or '-'}"
            + (f", from {src}" if src else '') + ")")


def not_tt_why(tx: dict, date_basis: str = 'settle') -> str:
    """The NOT_TT_WHY key of a row taxjson-convert-tt refuses to write
    ('' for a row it writes, or one with no line at all): what the `tjs
    events` view marks its line with, so a pasted copy is refused
    (backlog TJ-5212). The test is the writer's own, on the writer's
    own line (never the view's rounded one)."""
    try:
        line = tx_to_tt_line(tx, date_basis)
    except ValueError:
        return ''
    return _unwritable_cash(tx, line)[0] if line is not None else ''


def _unwritable_type(tx: dict, line: str) -> str:
    """Why the .tt line of a row does not carry the row's kind (its
    `type`, which the engines book it by and which is part of its id):
    '' for a row with no type, one whose line reads back with it, or
    one of the kinds whose cash differs (_unwritable_cash names those).
    A line the reader refuses is its own refusal there, as before. A
    round trip wrote such a row as an ordinary line with no word
    (backlog TJ-5210)."""
    import contextlib
    from taxjson.lib.row_types import IN_KIND_TYPES, TRANSFER_BOOK_VALUE_TYPE
    typ = str(tx.get('type') or '').strip()
    if not typ or typ in IN_KIND_TYPES + (TRANSFER_BOOK_VALUE_TYPE,):
        return ''
    nothing = "nothing was written"
    if tx.get('action') == 'TRANSFER' and typ == LOT_MOVE_TYPE:
        return (
            f"a row of type {typ} is one leg of a move between two of "
            f"your taxable accounts, which `tjs run` pairs with the "
            f"other account's leg by the rows' description (the peer "
            f"account, how the rows are signed): a .tt line carries no "
            f"description, and the leg would read back as a plain "
            f"transfer — {nothing}. Convert the account's parsed book "
            f"(the transfer rows, before the run pairs them).")
    if (tx.get('action') == 'TRANSFER' and typ == LISTING_JOURNAL_TYPE
            and f" {PAIR_KEY}=" not in line):
        return (
            f"a row of type {typ} is one leg of a journal between two "
            f"listings, joined to the other leg by its `journal_pair`, "
            f"and this row's ({tx.get('journal_pair')!r}) is not a "
            f"single word a `{PAIR_KEY}=` token can carry — {nothing}. "
            f"Fix the row in the book.")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            back = parse_tt_line(line, account_name=str(
                tx.get('account') or 'default'))
    except ValueError as e:
        if f" type={typ}" not in line or typ not in (
                RUN_TRADE_TYPES + RUN_TRANSFER_TYPES):
            return ''
        return (
            f"a row of type {typ} is a kind `tjs run` makes, and this "
            f"one lacks what its line needs — {nothing}. The reader's "
            f"refusal of the line: {e}")
    if back is None or str(back.get('type') or '').lower() == typ.lower():
        return ''
    return (
        f"a row of type {typ} is a kind no {tx.get('action')} line "
        f"carries: its .tt line would read back as "
        + (f"a row of type {back['type']}" if back.get('type') else
           "an ordinary row")
        + f", booked by another rule and under another id — {nothing}. "
          f"The line would be: {line!r}")


def _unwritable_cash(tx: dict, line: str) -> tuple:
    """(NOT_TT_WHY key, why) when the .tt line of a row would not move
    the cash the row moves on the days it moves it (the row's own trade
    or pay date and settlement date; lib/cash_legs `cash_meaning`: the
    legs, a later payment's date, a reward's own currency, the
    commission refunds), or the row's kind is one a .tt row cannot say;
    ('', '') for a row the line says. The line is read back the way
    `tjs run` reads it; one the reader refuses is its own refusal
    there, as before."""
    import contextlib
    from taxjson.lib.cash_legs import cash_leg_of, cash_meaning, same_cash_meaning
    from taxjson.lib.row_types import IN_KIND_TYPES, TRANSFER_BOOK_VALUE_TYPE
    typ = str(tx.get('type') or '')
    why_type = _unwritable_type(tx, line)
    if why_type:
        return 'row_type', why_type
    if typ in IN_KIND_TYPES + (TRANSFER_BOOK_VALUE_TYPE,):
        return 'in_kind', (
            f"a row of type {typ} is a "
            f"move in kind that `tjs run` booked from the account's "
            f"transfer rows (securities valued, no cash paid): a .tt "
            f"row cannot say it, and its line would read back as a "
            f"trade for cash — nothing was written. Convert the "
            f"account's parsed book (the transfer rows, before the run "
            f"books them), and keep the move's INKIND line or transfer "
            f"settings in the project.")
    # (the reader's warnings of the line, and the market lists' notes,
    # are the later read's to give: not said here)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            back = parse_tt_line(line, account_name=str(
                tx.get('account') or 'default'))
            same = back is None or same_cash_meaning(cash_meaning(tx),
                                                     cash_meaning(back))
    except ValueError:
        return '', ''
    td = str(tx.get('date') or '')
    sd = str(tx.get('date_settle') or '') or td
    if (back is not None and _is_date(td) and _is_date(sd)
            and not line.startswith('OPENING ')
            and (back['date'], back['date_settle']) != (td, sd)):
        # The ledgers walk a row's cash on its settlement date, and
        # order it by its trade date.
        return 'dates', (
            f"its .tt line would not keep the row's dates (dated {td}, "
            f"settled {sd}; the line reads back dated {back['date']}, "
            f"settled {back['date_settle']}): a line says a settlement "
            f"date on or after the trade or pay date, at most "
            f"{MAX_DATE_GAP_DAYS} days later, and the FX-on-cash ledgers "
            f"would walk this row's cash on another day — nothing was "
            f"written. Fix the row's dates in the book. The line would "
            f"be: {line!r}")
    if same:
        return '', ''
    kind = cash_leg_of(tx)
    action = str(tx.get('action') or '')
    if kind and f"{CASH_LEG_KEY}=" not in line:
        why = (f"it is a `{CASH_LEG_KEY}={kind}` row ("
               + ("the row's own `cash_leg` field says so"
                  if tx.get('cash_leg') else
                  "lib/cash_legs: its wording, type and source say so")
               + ") and "
               + (f"{_a(action)} line has no place for the token"
                  if action in ('INTEREST', 'FEE') else
                  f"the token does not go on this line: "
                  f"{_cash_leg_problem(action, kind, bool(tx.get('corp_event_id')), typ.lower(), bool(corp_cash_tt(tx.get('corp_cash'))))}"))
    else:
        why = ("the line holds less than the row (a type, a cash field "
               "or a token value a .tt line cannot carry)")
    return 'cash_leg', (
        f"its .tt line would move "
        f"other foreign cash than the row does in the FX-on-cash "
        f"ledgers: {why} — nothing was written. The line would be: "
        f"{line!r}")


def _refuse_unwritable_cash(input_path: Path, i: int, tx: dict,
                            line: str) -> None:
    """Refuse a row no .tt line can say (_unwritable_cash), naming it:
    never a line that reads back as another amount of currency, with no
    word (backlog TJ-5036)."""
    from taxjson.lib.cli_diag import InputContentError
    why = _unwritable_cash(tx, line)[1]
    if why:
        raise InputContentError(f"{_row_named(input_path, i, tx)}: {why}")


def json_to_tt_lines(input_path: Path, date_basis: str = 'settle'):
    transactions = _json_book_rows(input_path)
    skipped = 0
    mults = []
    for i, tx in enumerate(transactions):
        line = tx_to_tt_line(tx, date_basis=date_basis)
        if line is None:
            skipped += 1
            continue
        _refuse_unwritable_cash(input_path, i, tx, line)
        m = tx.get('multiplier')
        try:
            if (m not in (None, '') and float(m) not in (1.0, 100.0)
                    and not any(_MULT_RE.match(t)
                                for t in line.split()[
                                    6 if line.startswith('OPENING ')
                                    else 9:])):
                mults.append(f"{tx.get('symbol')} x{size_text(float(m))}")
        except (TypeError, ValueError):
            mults.append(f"{tx.get('symbol')} x{m!r}")
        yield line
    if skipped:
        emit_line(
            f"note: skipped {skipped} transaction(s) with no .tt representation",
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


# The 1% of qty x price (x size) a total may differ by, with its floor
# in the line's currency: a cent of rounding on a tiny total is never a
# typo, a 5x total always is.
TOLERANCE_PCT = 0.01
TOLERANCE_FLOOR = 0.05


def tolerance(expected: float) -> float:
    """How far a total may be from qty x price +/- fee (`expected`)."""
    return max(TOLERANCE_FLOOR, TOLERANCE_PCT * max(abs(expected), 1.0))


# A .tt NOTOWN line (backlog TJ-0075): in the folder of the taxable
# account the shares arrived in, it says that one transfer-in and the
# transfer-out the run paired it with are NOT one move of your own.
NOTOWN_KEYWORD = "NOTOWN"
NOTOWN_FORM = "NOTOWN <date> <symbol> <qty> <from-account> <from-date>"


def parse_not_own_line(line: str, source: str = ""
                       ) -> Optional[Dict[str, Any]]:
    """`NOTOWN <date> <symbol> <qty> <from-account> <from-date>` ->
    {date, symbol, quantity, from_account, from_date, line}, or None when
    the line is not a NOTOWN line. Raises ValueError naming the form on
    a malformed one (`#` starts a comment)."""
    where = f"{source}: " if source else ""
    parts = strip_tt_comment(line).split()
    if not parts or parts[0] != NOTOWN_KEYWORD:
        return None
    shown = line.strip()
    toks = parts[1:]
    if len(toks) >= 2 and not _is_date(toks[0]) and _is_date(toks[1]):
        raise ValueError(
            f"{where}malformed {NOTOWN_KEYWORD} line — a .tt line is date "
            f"first (expected `{NOTOWN_FORM}`): {shown!r}")
    if len(toks) != 5:
        raise ValueError(
            f"{where}malformed {NOTOWN_KEYWORD} line — expected "
            f"`{NOTOWN_FORM}` (the transfer-in's date, symbol and units, "
            f"then the account and date of the transfer-out it was paired "
            f"with): {shown!r}")
    date, symbol, qty, frm, fdate = toks
    for d, what in ((date, "date"), (fdate, "from-date")):
        if not _is_date(d):
            raise ValueError(
                f"{where}{NOTOWN_KEYWORD} {what} {d!r} is not a valid "
                f"YYYY-MM-DD date (expected `{NOTOWN_FORM}`): {shown!r}")
    from taxjson.lib.brokerages.base import parse_strict_number
    try:
        # Strict, like a .tt row's quantity: a decimal comma is refused.
        if not re.fullmatch(r"[0-9.,eE+]+", qty):
            raise ValueError(qty)       # no sign, `$` or parentheses
        q = parse_strict_number(qty, field="quantity")
    except ValueError:
        q = None
    if q is None or not math.isfinite(q) or q <= 0:
        raise ValueError(
            f"{where}{NOTOWN_KEYWORD} quantity {qty!r} is not the number of "
            f"units that arrived, positive (expected `{NOTOWN_FORM}`): "
            f"{shown!r}")
    return {"date": date, "symbol": _book_symbol(symbol), "quantity": float(q),
            "from_account": frm, "from_date": fdate, "line": shown}


# Stamped into `description` by parse_tt_line on a hand-written .tt
# TRANSFER row that carries the DECLARED token (opt-in: not every .tt
# TRANSFER). A zero-net segment containing a declared leg is the
# user ANSWERING the AmbiguousTransferDateError ("add a counter-TRANSFER
# to a .tt file so the pair nets out") — the near-trade refusal must not
# re-refuse the declared resolution it demanded (2026-09 audit).
MANUAL_TRANSFER_DECLARATION = 'manual declaration (.tt)'
