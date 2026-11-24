"""The evidence a row packs into a string, as records and back (step 6b).

Five row fields (model/transaction.py) hold a list or a record in one
string, each in the spelling of the code that writes it:

- `broker_lots`: the lots an IB closing trade closed (its ClosedLot rows),
  "<open date> <quantity> <basis>" joined by ";", quantity written `.10g`
  and basis `.2f`; or "invalid" when a lot did not read
  (lib/brokerages/ib_extractor `_closed_lot`). Read by
  `find-missing-history --write-purchases` (lib/missing_history).
- `commission_refunds`: the commission refunds IB folded into a trade's
  cost, "<date> <amount>" joined by "; " in date order, the amount a
  float's repr (ib_extractor `_ib_fold_refund_into`); in a .tt line the
  token `refunds=`, "<date>:<amount>" joined by ";" (lib/tt_format). Read
  by the cash ledger (lib/fx_cash_v2 `_refund_legs`).
- `opening_cut_legs`: the share rows an OPENING snapshot replaced,
  "<date> <quantity> <price> <action>[ <symbol>]" joined by ";", the
  numbers a float's repr; the symbol is the row's own when a rename
  made it the snapshot's (lib/opening `_note_cut_legs`). Read by the assignment
  premium ledger (engine/premium_ledger `_AssignPremiumLedger`). A leg of
  action UNKNOWN_OPENING_LEG is a `.tt` `OPENING ... cost=unknown` line the
  snapshot replaced (price 0; backlog TJ-5007): read by the openings
  (lib/opening `unknown_opening_replaced`), never by the ledger.
- `corp_cash`: the cash a corporate action paid (cash in lieu, boot),
  "<amount> <CUR>[ @<paid>]" joined by "; ", the amount `.10g`
  (lib/corp_actions `_corp_cash`); in a .tt line the token `cash=`, its
  spaces made colons (lib/tt_format). Read by fx-cash
  (lib/cash_legs `_corp_cash_parts`; bin/taxjson_fx_cash `_cash_by_day`).
- `settle_native`: a futures settlement part's native facts, "<leg>
  <money> <P/L> <CUR>[ <rate>]", the numbers their Decimal's str
  (lib/futures `_part`, `stamp_rate`); in a .tt line `settle=`, its
  spaces made commas. Read by the Canadian engine's pools and its
  re-average across accounts (lib/futures `engine_pool_key`,
  `pool_across_accounts`) and the US warning of a contract long in one
  broker account and short in another (`opposite_broker_positions`).

Each parser keeps its reader's rules: all or nothing for broker lots, a
part that does not read skipped for refunds and corp cash, one leg at a time
for the cut legs (the ledger reads a leg only when an assignment needs it,
and reads it whole: a bad number in a leg it then skipped raised nothing
before; no writer makes one), None for settlement facts that do not
read. A record's numbers are the Decimals of its tokens; a reader that
wants a float takes float() of one, the float its token reads as. The
writers add to a field through `add_broker_lot`,
`add_commission_refund` and `join_cut_legs`, which keep what the field
held as written (a NaN or an infinity too, as the writers' f-strings
wrote them), and stamp a settlement's rate with `stamp_settle_rate`; each
formatter writes its writer's spelling, so `format(parse(s)) == s` for
what the writers make that their reader reads (an IB lot dated 2025-02-30
makes the reader refuse the field: None). The .tt tokens of `corp_cash`
and `settle_native` write the row's text as it is, and read a token's
numbers into the formatters' spelling of the same numbers."""
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional, Tuple

from taxjson.model.money import exact_decimal, text_decimal
from taxjson.model import money as M

# broker_lots when a ClosedLot row did not read: the drafter then uses the
# trade's total basis (ib_extractor `_closed_lot`).
BROKER_LOTS_INVALID = "invalid"


class EvidenceError(ValueError):
    """A packed field's part that does not read."""


@dataclass(frozen=True, slots=True)
class BrokerLot:
    """One lot a closing trade closed: when it opened, how much, its cost."""
    open_date: str
    quantity: Decimal
    cost: Decimal


@dataclass(frozen=True, slots=True)
class CommissionRefund:
    """A refund of the trade's commission, on its own date."""
    date: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CutLeg:
    """A share row an OPENING snapshot replaced. `symbol`: the row's
    own symbol when it is not the snapshot's (a rename on or after the
    row made it the snapshot's; backlog TJ-5005), else ''."""
    date: str
    quantity: Decimal
    price: Decimal
    action: str
    symbol: str = ''


def _token(d: Decimal) -> str:
    """A number as the writers spell it: a float's repr (nan and inf
    included; a 17-digit e+16 token stays one)."""
    return repr(float(d))


def _number(token: str) -> Decimal:
    """A token as the Decimal it spells (model/money `text_decimal`); one
    float() would not read is refused (the readers took float() of these
    tokens). An exponent past Decimal's is the float the token reads as,
    inf or 0.0."""
    try:
        return text_decimal(token)
    except ValueError:
        raise EvidenceError(f"not a number: {token!r}") from None


# --- broker_lots -----------------------------------------------------------

def parse_broker_lots(text: Optional[str]) -> Optional[Tuple[BrokerLot, ...]]:
    """The lots of a `broker_lots` string, or None: when there are none,
    when it is BROKER_LOTS_INVALID, or when any lot does not read (three
    tokens: an ISO date, a quantity not 0 or less, a cost not below 0).
    The date is written back as date.isoformat() gives it."""
    text = str(text or '').strip()
    if not text or text == BROKER_LOTS_INVALID:
        return None
    out = []
    for part in text.split(';'):
        bits = part.split()
        if len(bits) != 3:
            return None
        try:
            day = date.fromisoformat(bits[0]).isoformat()
            q, c = _number(bits[1]), _number(bits[2])
        except ValueError:
            return None
        if float(q) <= 0 or float(c) < 0:
            return None
        out.append(BrokerLot(day, q, c))
    return tuple(out) or None


def broker_lot(open_date: str, quantity: float, basis: float) -> BrokerLot:
    """The lot the IB writer records: its quantity as `.10g` spells it,
    its basis to the cent (`.2f`)."""
    return BrokerLot(open_date, Decimal(f"{quantity:.10g}"), Decimal(f"{M.units(basis, 2):.2f}"))


def format_broker_lot(lot: BrokerLot) -> str:
    """The lot as the writer records it: `broker_lot`'s cut, a stored
    figure (`units`), not a shown one (TJ-3351)."""
    return f"{lot.open_date} {float(lot.quantity):.10g} {M.units(float(lot.cost), 2):.2f}"


def format_broker_lots(lots: Iterable[BrokerLot]) -> str:
    """The lots joined as the writer joins them (";")."""
    return ";".join(format_broker_lot(lot) for lot in lots)


def add_broker_lot(text: Optional[str], lot: BrokerLot) -> str:
    """`broker_lots` with one more lot after what it held, as written."""
    return f"{text};{format_broker_lot(lot)}" if text else format_broker_lot(lot)


# --- commission_refunds ----------------------------------------------------

def parse_commission_refunds(text: Optional[str]) -> Tuple[CommissionRefund, ...]:
    """The refunds of a `commission_refunds` string, in its order; a part
    that does not read (two tokens: a %Y-%m-%d date, an amount) is
    skipped, as the cash ledger skips it."""
    out = []
    for part in str(text or '').strip().split(';'):
        bits = part.split()
        if len(bits) != 2:
            continue
        try:
            datetime.strptime(bits[0], "%Y-%m-%d")
            out.append(CommissionRefund(bits[0], _number(bits[1])))
        except ValueError:
            continue
    return tuple(out)


def commission_refund(day: str, amount: float) -> CommissionRefund:
    """The refund the IB writer records: its amount rounded to 10 places."""
    return CommissionRefund(day, Decimal(repr(M.units(float(amount), 10))))


def _refund_order(part: str) -> Tuple[str, float]:
    """The writer's order of refund parts: by date, then amount."""
    bits = part.split()
    return (bits[0], float(bits[1]))


def _join_refunds(parts: Iterable[str]) -> str:
    """Refund parts joined as the writer joins them ("; "), in its order."""
    return "; ".join(sorted(parts, key=_refund_order))


def _refund_part(r: CommissionRefund) -> str:
    return f"{r.date} {_token(r.amount)}"


def format_commission_refunds(refunds: Iterable[CommissionRefund]) -> str:
    """The refunds joined as the writer joins them ("; "), in its order."""
    return _join_refunds(_refund_part(r) for r in refunds)


def add_commission_refund(text: Optional[str], refund: CommissionRefund) -> str:
    """`commission_refunds` with one more refund, the parts it held kept
    as written and all put in the writer's order (a part that does not
    read raises, as it did)."""
    parts = [x for x in str(text or '').split('; ') if x]
    return _join_refunds(parts + [_refund_part(refund)])


# A part of the .tt token `refunds=` (lib/tt_format): the row's part
# with its space made a colon, `<date>:<amount>`.
# What a refund's amount must not write as (`_token`): past a float's
# range, no number, or nothing.
_NO_REFUND = ("inf", "-inf", "nan", "0.0", "-0.0")
_REFUND_TT_PART = re.compile(r'^\d{4}-\d{2}-\d{2}:-?\d[\d.eE+-]*$')


def commission_refunds_tt(text: Optional[str]) -> str:
    """A row's `commission_refunds` as the .tt token's value: the refunds
    the cash ledger reads of it (`parse_commission_refunds`: a part that
    does not read moves no cash and is left out), each `<date>:<amount>`,
    joined by ";"; '' when there are none (backlog TJ-5036)."""
    return ';'.join(f"{r.date}:{_token(r.amount)}" for r in parse_commission_refunds(text))


def commission_refunds_of_tt(token: str) -> str:
    """The row's `commission_refunds` of a .tt `refunds=` value, as the
    writer spells it (`format_commission_refunds`: date order, each
    amount a float's repr); EvidenceError when a part is not
    `<date>:<amount>`, its date is no day of the calendar, or its amount
    is no finite number or 0."""
    parts = token.split(';')
    if not all(_REFUND_TT_PART.match(p) for p in parts):
        raise EvidenceError(f"not a trade's commission refunds: {token!r}")
    refunds = parse_commission_refunds('; '.join(p.replace(':', ' ') for p in parts))
    if len(refunds) != len(parts) or any(
            _token(r.amount) in _NO_REFUND for r in refunds):
        raise EvidenceError(f"a date or an amount that does not read: {token!r}")
    return format_commission_refunds(refunds)


# --- opening_cut_legs ------------------------------------------------------

def split_cut_legs(text: Optional[str]) -> Tuple[str, ...]:
    """The legs of an `opening_cut_legs` string, unread: the ledger
    reads one with parse_cut_leg when an assignment needs it."""
    return tuple(leg for leg in (text or '').split(';') if leg)


def parse_cut_leg(leg: str) -> CutLeg:
    """One leg: a date, a quantity, a price and an action, then the
    row's own symbol when a rename made it the snapshot's (EvidenceError
    when it does not read). The ledger read a leg's numbers only where it
    used them, so a bad number in a leg it skipped did not raise; no
    writer makes one, and a leg is read whole now."""
    bits = leg.split()
    if len(bits) not in (4, 5):
        raise EvidenceError(f"an opening's cut leg is four or five tokens, not {leg!r}")
    return CutLeg(bits[0], _number(bits[1]), _number(bits[2]), bits[3], *bits[4:])


def cut_leg_symbol(leg: str) -> str:
    """The symbol an unread leg's row had when it is not the snapshot's
    ('' for the snapshot's own): what the ledger files a leg under
    before it reads one."""
    bits = leg.split()
    return bits[4] if len(bits) == 5 else ''


# The action of a leg that records a `.tt` `OPENING ... cost=unknown`
# line a snapshot replaced (the row's action; backlog TJ-5007).
UNKNOWN_OPENING_LEG = 'OPENING_BALANCE'


def cut_leg_action(leg: str) -> str:
    """The action of an unread leg ('' when it has none): what tells an
    unknown-cost opening's leg (UNKNOWN_OPENING_LEG) from a share row's
    before the ledger files one."""
    bits = leg.split()
    return bits[3] if len(bits) in (4, 5) else ''


def parse_cut_legs(text: Optional[str]) -> Tuple[CutLeg, ...]:
    """Every leg of an `opening_cut_legs` string, read whole (EvidenceError
    for one that does not read); the ledger reads them one at a time."""
    return tuple(parse_cut_leg(leg) for leg in split_cut_legs(text))


def cut_leg(day: str, quantity: float, price: float, action: str,
            symbol: str = '') -> CutLeg:
    """The leg lib/opening writes: its numbers as their repr."""
    return CutLeg(day, Decimal(repr(float(quantity))), Decimal(repr(float(price))), action,
                  symbol)


def format_cut_leg(leg: CutLeg) -> str:
    return (f"{leg.date} {_token(leg.quantity)} {_token(leg.price)} {leg.action}"
            + (f" {leg.symbol}" if leg.symbol else ""))


def format_cut_legs(legs: Iterable[CutLeg]) -> str:
    """The legs joined as the writer joins them (";")."""
    return ";".join(format_cut_leg(leg) for leg in legs)


def join_cut_legs(text: Optional[str], legs: Iterable[CutLeg]) -> str:
    """`opening_cut_legs` with `legs` after what it held, as written."""
    return ";".join(filter(None, [str(text or '')] + [format_cut_leg(leg) for leg in legs]))


# --- corp_cash -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CorpCash:
    """Cash a corporate action paid (cash in lieu of a fraction, boot):
    `paid` is the day the broker paid it when that is later than the
    row's own date, '' when it is the row's date."""
    amount: Decimal
    currency: str
    paid: str = ""


def parse_corp_cash(text: Optional[str]) -> Tuple[CorpCash, ...]:
    """The cash of a `corp_cash` string, in its order; a part that does
    not read (an amount and a currency, then `@<paid>` or not) is
    skipped, as fx-cash skips it. The currency is upper-cased; a bare
    `@` is cash paid on the row's date."""
    out = []
    for part in str(text or '').split(';'):
        bits = part.split()
        paid = ''
        if len(bits) == 3 and bits[2].startswith('@'):
            paid = bits.pop()[1:]
        if len(bits) != 2:
            continue
        try:
            amount = _number(bits[0])
        except EvidenceError:
            continue
        out.append(CorpCash(amount, bits[1].upper(), paid))
    return tuple(out)


def corp_cash(amount: float, currency: str, paid: str = '') -> CorpCash:
    """The cash lib/corp_actions records: its amount as `.10g` spells it,
    its currency upper-cased."""
    return CorpCash(Decimal(f"{float(amount):.10g}"), str(currency).upper(), str(paid or ''))


# Below this, `.10g` writes an exponent ("5e-05").
_CASH_TINY = Decimal("0.0001")


def _cash_text(amount: Decimal) -> str:
    """An amount as written, the same number: its Decimal's digits as
    they stand (a .tt token's "7.50" stays "7.50"); where those need an
    exponent or the amount is below 0.0001, the `.10g` of its float when
    that is the same number ("1e+10", "5e-05"), else the Decimal's own
    str ("1.2345678901234E+20"); `.10g` for a NaN or an infinity. An
    amount `corp_cash` made comes back as `.10g` spelled it."""
    text = str(amount)
    if not amount.is_finite():
        return f"{float(amount):.10g}"
    if 'E' not in text and (not amount or amount.copy_abs() >= _CASH_TINY):
        return text
    short = f"{float(amount):.10g}"
    return short if exact_decimal(short) == amount else text


def format_corp_cash_part(cash: CorpCash) -> str:
    return f"{_cash_text(cash.amount)} {cash.currency}" + (f" @{cash.paid}" if cash.paid else '')


def format_corp_cash(cash: Iterable[CorpCash]) -> str:
    """The cash joined as the writer joins it ("; ")."""
    return '; '.join(format_corp_cash_part(c) for c in cash)


# A part of the .tt token `cash=` (lib/tt_format): the row's part
# with its spaces made colons, `<amount>:<CUR>[:@<date>]`.
_CORP_CASH_TT_PART = re.compile(r'^-?\d[\d.eE+-]*:[A-Z0-9]+(?::@\d{4}-\d{2}-\d{2})?$')


def corp_cash_tt(text: Optional[str]) -> str:
    """A row's `corp_cash` as the .tt token's value: each part's spaces
    made colons, the parts joined by ";" (convert_tt writes the token
    only when what this gives holds no space or #)."""
    return ';'.join(p.strip().replace(' ', ':') for p in str(text or '').split(';'))


def corp_cash_of_tt(token: str) -> str:
    """The row's `corp_cash` of a .tt `cash=` value, each amount as
    `format_corp_cash` writes it (a typed 1e5 is 100000, 07.5 is 7.5:
    the same number, so the row reads back to itself); EvidenceError
    when a part is not `<amount>:<CUR>` or `<amount>:<CUR>:@<date>`,
    or its amount is no number (1.2.3: fx-cash skipped the part, the
    cash lost) or past a float's range (backlog TJ-2843)."""
    parts = token.split(';')
    if not all(_CORP_CASH_TT_PART.match(p) for p in parts):
        raise EvidenceError(f"not the cash an event paid: {token!r}")
    cash = parse_corp_cash('; '.join(p.replace(':', ' ') for p in parts))
    if len(cash) != len(parts) or not all(math.isfinite(float(c.amount)) for c in cash):
        raise EvidenceError(f"an amount that is no finite number: {token!r}")
    return format_corp_cash(cash)


# --- settle_native ---------------------------------------------------------

# A settlement part's leg: a long's or a short's opening or close.
SETTLE_LEGS = ("LO", "LC", "SO", "SC")


@dataclass(frozen=True, slots=True)
class SettleNative:
    """The native facts of a futures settlement part (lib/futures): its
    leg, the native money its units carry, its native P/L, its currency
    ('-' for none), and the rate its conversion applied (None before
    conversion)."""
    leg: str
    money: Decimal
    pl: Decimal
    currency: str
    rate: Optional[Decimal] = None


def _decimal(token: str) -> Decimal:
    """A token as the Decimal it spells (model/money `exact_decimal`); EvidenceError when
    Decimal does not read it, or it is a NaN, an infinity or past a
    float's range (a settlement's money, P/L and rate are amounts: an
    sNaN or a 1E+999999999 rate raised in the next conversion's stamp)."""
    d = exact_decimal(token)
    if d is None or not d.is_finite() or not math.isfinite(float(d)):
        raise EvidenceError(f"not a finite number: {token!r}")
    return d


def parse_settle_native(text: Optional[str]) -> Optional[SettleNative]:
    """The facts of a `settle_native` string, or None when it is empty
    or does not read (a book settled by an older taxjson): four tokens,
    or five with the rate, the first one of SETTLE_LEGS, the numbers
    finite, a rate above 0 (a conversion's: 0 made the re-averaged P/L
    0)."""
    parts = str(text or '').split()
    if len(parts) not in (4, 5) or parts[0] not in SETTLE_LEGS:
        return None
    try:
        money, pl = _decimal(parts[1]), _decimal(parts[2])
        rate = _decimal(parts[4]) if len(parts) == 5 else None
    except EvidenceError:
        return None
    if rate is not None and rate <= 0:
        return None
    return SettleNative(parts[0], money, pl, parts[3], rate)


def settle_native(leg: str, money: Decimal, pl: Decimal, currency: Optional[str]) -> SettleNative:
    """The facts lib/futures records on a part: its currency upper-cased,
    '-' for none."""
    return SettleNative(leg, money, pl, (currency or '').upper() or '-')


def format_settle_native(facts: SettleNative) -> str:
    """The facts as lib/futures writes them: each number its Decimal's
    str, the rate last once a conversion applied one."""
    text = f"{facts.leg} {facts.money} {facts.pl} {facts.currency}"
    return text if facts.rate is None else f"{text} {facts.rate}"


def settle_leg(text: Optional[str]) -> str:
    """The leg a `settle_native` string starts with: its first token
    when that is one of SETTLE_LEGS, '' otherwise (an empty field, a
    part of no units). The facts may not read (an older book's): the
    leg alone still says which pool the part is in."""
    parts = str(text or '').split()
    return parts[0] if parts and parts[0] in SETTLE_LEGS else ''


def stamp_settle_rate(text: Optional[str], rate: object) -> Optional[str]:
    """`settle_native` with the rate a conversion applied, as its
    Decimal's str: added to four tokens, as written (read or not, as
    lib/futures did); composed with the rate of five that read (rate x
    their rate), so the rate always takes the native P/L to the currency
    the row is in now. None when there is nothing to stamp: an empty
    field, five tokens that do not read, or any other count."""
    v = str(text or '')
    parts = v.split()
    if len(parts) == 4:
        return f"{v} {Decimal(str(rate))}"
    facts = parse_settle_native(v)
    if len(parts) == 5 and facts is not None and facts.rate is not None:
        return " ".join(parts[:4] + [str(facts.rate * Decimal(str(rate)))])
    return None


def settle_native_tt(text: Optional[str]) -> str:
    """A row's `settle_native` as the .tt `settle=` value: its tokens
    joined by commas ('' for none)."""
    return ','.join(str(text or '').split())


def settle_native_of_tt(token: str) -> Tuple[str, Optional[SettleNative]]:
    """(the row's `settle_native`, its facts) of a .tt `settle=` value:
    the facts as `format_settle_native` writes them (each number its
    Decimal's str, the same number: a typed 1e5 is 1E+5; the currency
    upper-cased, as `settle_native` writes it: the re-average pools by
    it), so the row reads back to itself; the facts None, and the
    value's commas made spaces, when it does not read or a field is
    empty (`LO,,100,5,USD` read its P/L as the money, `,SO,...` was
    pooled as a long: backlog TJ-2837, TJ-2840)."""
    fields = token.split(',')
    facts = None if '' in fields else parse_settle_native(' '.join(fields))
    if facts is not None and facts.currency != facts.currency.upper():
        facts = SettleNative(facts.leg, facts.money, facts.pl, facts.currency.upper(), facts.rate)
    return (' '.join(fields) if facts is None else format_settle_native(facts)), facts
