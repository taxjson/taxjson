"""`tjs div-estimate`: upcoming dividends projected from each held
position's own dividend history in the books. Planning numbers only,
never a filing figure (tax-logic CA-PLAN-06 / US-PLAN-07).

Per payer (a listing, across the accounts that hold it):
- the payment frequency from its past pay dates: the grid (weekly,
  monthly, quarterly, semi-annual or annual), anchored at the latest
  payment on it, that most of the last payments fall on; a payment off the
  grid (a special) or a stretch of empty slots (payments made while
  the position was not held) does not break it. A history too short
  to settle one (one to three payments, each on the quarterly grid
  anchored at the latest) is projected quarterly, shown as assumed,
  until the books contradict it (`assumed_quarterly`). No grid fits,
  and not assumed: irregular (its estimate is pro rata from the
  trailing-12-month amount per share);
- its latest per-share amount (gross, in the payment's currency) and
  its trailing-12-month amount per share. A payment before a stock
  split is read in today's share denomination (the split's ratio
  scales its share count), so the forecast is not multiplied by the
  split (every split in the books, whatever the as-of date: the shares
  are the books' current holdings); a payment in lieu on lent shares
  beside the dividend on the rest is one payment on all of them; a
  dividend charged on a short position (a negative row while the books
  put the account short the symbol, splits and renames counted) is an
  expense, not a correction of earlier income; a dividend paid while
  the books put it short counts, and the command warns (the books
  likely miss a trade: backlog TJ-0073).

Per position (account, listing) held now: the shares, the next pay
dates on the payer's schedule and their amounts (latest per-share x
shares held), converted to the base currency at the books' latest rate
(work/to_base.csv; never a network call). The history used is in one
currency, the one that account's latest payment was paid in (payments
in another currency, another broker's conversion, are flagged and left
out). Each projected payment is dated for the tax year by the same
rule as divs-sum (characterize/income_dating.IncomeRules.income_date on a copy
of the payer's latest row moved to the projected date: a Canadian
trust's distribution by its record date, a listed US RIC's January
dividend in the year before). Withholding uses the rate the latest
payment in that account shows (its TAX rows over its gross, every
broker account of a combined account together; a TAX row in another
currency converted at the pay date's rate), where it has one.

This module is pure: the command (cli/summaries.cmd_div_estimate) reads
the books and hands the rows in.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from taxjson.lib.json_contract import SCHEMAS
from taxjson.model import money as M

SCHEMA_VERSION = SCHEMAS["div-estimate"]

# (name, months between payments (0: by days), days between payments)
FREQUENCIES: Tuple[Tuple[str, int, int], ...] = (
    ("weekly", 0, 7),
    ("monthly", 1, 30),
    ("quarterly", 3, 91),
    ("semi-annual", 6, 182),
    ("annual", 12, 365),
)
# A projected payment this many days overdue (by its frequency) is taken
# as missing from the books, not as still to come.
GRACE_DAYS = {"weekly": 3, "monthly": 7, "quarterly": 15, "semi-annual": 20,
              "annual": 30}
# The frequency is read from the last LOOKBACK payments.
LOOKBACK = 12
# A payment is on a frequency's grid when it is within GAP_TOL of a
# period from its slot (a monthly payer's date drifts a few days with
# weekends and the broker's posting; a quarterly one's a few weeks).
GAP_TOL = 0.25
_INCOME = ("DIVIDEND", "DIVIDEND_IN_LIEU")
# The rows that move an account's share balance (the sign of the
# balance tells a short position's dividend expense from a reversal).
_TRADES = ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER")


@dataclass
class Payment:
    pay_date: str
    per_share: float
    gross: float
    qty: float
    currency: str
    account: str
    tax: float
    row: Dict[str, Any] = field(repr=False, default_factory=dict)
    # The broker account of a taxjson account that combines several
    # (combined_broker_accounts; "" when the rows do not name one).
    source: str = ""
    # Withholding posted in another currency than the payment's
    # ({currency: amount}, signed): `tax` is in the payment's own
    # currency only; `estimate` converts these at the pay date's rate
    # before it reads a rate (a CAD tax over a USD dividend read 2.70
    # over 10 as 27%: backlog TJ-0548).
    tax_fx: Dict[str, float] = field(default_factory=dict)


def _iso(d: date) -> str:
    return d.isoformat()


def add_months(iso: str, n: int) -> str:
    d = date.fromisoformat(iso)
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return _iso(date(y, m, min(d.day, calendar.monthrange(y, m)[1])))


def parse_window(token: str) -> Optional[int]:
    """Months in a forward window token (6m, 1y, 13w, 90d), or None;
    0 for a zero-length one ('0m', '0y'), which the caller refuses as
    it refuses --months 0 (backlog TJ-0039: it was read as one month).
    A few days or weeks still count as one month."""
    m = re.fullmatch(r"\s*(\d+)\s*([dwmy])\s*", str(token or "").lower())
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    if n == 0:
        return 0
    months = {"d": n / 30.4375, "w": n * 7 / 30.4375, "m": n,
              "y": n * 12}[u]
    return max(1, int(round(months)))


def horizon_problem(as_of: str, months: int,
                    tax_year: int) -> Optional[str]:
    """Why an estimate as of `as_of` over `months` months for `tax_year`
    cannot be computed: the window, or the January after the tax year,
    reaches past 9999-12-31, or the 12-month look-back reaches before
    0001-01-01 (`--date 9999-09-01 --months 120` raised "year 10009 is
    out of range" from inside the arithmetic: 2026-10 audit A30). None
    when it can."""
    try:
        d = date.fromisoformat(str(as_of))
    except ValueError:
        return f"--date {as_of!r} is not a YYYY-MM-DD date"
    try:
        d - timedelta(days=365)
    except OverflowError:
        return (f"--date {as_of}: the 12-month look-back reaches before "
                f"0001-01-01")
    try:
        add_months(as_of, int(months))
    except (ValueError, OverflowError):
        return (f"--date {as_of} with a {months}-month window reaches "
                f"past 9999-12-31")
    try:
        add_months(f"{int(tax_year):04d}-12-31", 1)
    except (ValueError, OverflowError):
        return (f"tax year {tax_year}: the projection runs to January "
                f"{int(tax_year) + 1}, past 9999-12-31")
    return None


class Payments(dict):
    """{symbol: [Payment]} that remembers the rows it was read from, so
    `estimate` can read them again as of its own date (a reversal or a
    share count dated after the as-of date must not change what the
    books said on it: 2026-10 audit R09; a later split still sets the
    denomination, the current holdings'). `notes`: {(account, symbol):
    [what the reading had to decide without the books' say-so]}, shown
    as the position's flags. `short_paid`: {(account, symbol): [the pay
    dates of the positive rows paid while the books put the account
    short the symbol]}: counted as income, and the command warns
    (backlog TJ-0073). What `assumed_quarterly` reads: `held`: {symbol
    (now): [(account, its [(date, signed share balance after it)], by
    date)]} (`_held_now`); `paid`: {symbol (now): [the date of every
    income row of it with money left after reversals, whatever its
    currency or share count]}; `ends`: {account: the date of its last
    row in the books, whatever the as-of date} (`_paid_and_ends`)."""
    rows: List[Tuple[str, Dict[str, Any]]] = []
    notes: Dict[Tuple[str, str], List[str]] = {}
    short_paid: Dict[Tuple[str, str], List[str]] = {}
    held: Dict[str, List[Tuple[str, List[Tuple[str, float]]]]] = {}
    paid: Dict[str, List[str]] = {}
    ends: Dict[str, str] = {}


def _money(v: float) -> str:
    return f"{M.shown(v, 2):,.2f}"


def _held(rows: List[Tuple[str, Dict[str, Any]]],
          ok: Callable[[Dict[str, Any]], Optional[Tuple[str, str, str]]],
          splits: List[Tuple[str, str, str, float]]
          ) -> Tuple[Dict[Tuple[str, str], List[Tuple[str, float]]],
                     Dict[Tuple[str, str, str], List[Tuple[str, float]]],
                     Dict[Tuple[str, str], List[List[Tuple[str, float]]]]]:
    """({(account, symbol): [(date, signed share balance after it)]},
    {(account, broker account, symbol): [(date, balance)]}, {(account,
    symbol after the last of `splits`): the parts of the first map's
    lists that are its security's}) by trade
    date: each account's trade rows (_TRADES; `ok` reads a row's
    (action, symbol, date), or None to leave it out), scaled by every
    split of `splits` (date, symbol, new symbol, ratio) and moved to the
    new symbol by a rename. On one date an opening balance comes first,
    then the split (effective at the open), then the day's trades. The
    raw quantities alone read 100 shares, a 2-for-1 split and a sale of
    150 as short 50, and a reversal of the long payment as a short's
    expense (2026-10-25 audit planning-us#3). The second map walks each
    broker account of a taxjson account that combines several (the
    rows' source_account) on its own: one broker account's short is not
    the other's long (backlog TJ-0558). A symbol a trade row naming no
    broker account moves (a .tt OPENING line) has no entry there: its
    broker accounts' own balances are incomplete, the account's decide.
    The third map cuts the lists at each rename: the old symbol's
    entries to the rename's (its last, at 0) are the renamed security's
    and go on with it, those after it are another security's that took
    the old symbol up (a ticker reused: backlog TJ-5052)."""
    walk: Dict[str, List[tuple]] = {}
    for acct, t in rows:
        key = ok(t)
        if key is None or key[0] not in _TRADES:
            continue
        try:
            q = float(t.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        walk.setdefault(acct, []).append(
            (key[2], 0 if key[0] == "OPENING_BALANCE" else 2,
             str(t.get("time") or ""), key[1], q, "",
             str(t.get("source_account") or "")))
    out: Dict[Tuple[str, str], List[Tuple[str, float]]] = {}
    by_src: Dict[Tuple[str, str, str], List[Tuple[str, float]]] = {}
    parts: Dict[Tuple[str, str], List[List[Tuple[str, float]]]] = {}
    for acct, ts in walk.items():
        bal: Dict[Tuple[str, str], float] = {}      # (source, symbol)
        mixed = set()       # symbols a row naming no broker account moved
        cut: Dict[str, int] = {}    # symbol: where its security's own entries start
        carried: Dict[str, List[List[Tuple[str, float]]]] = {}  # earlier names'
        evs = [(d, 1, "", sym, ratio, new, "")
               for d, sym, new, ratio in splits]
        for d, rank, _tm, sym, q, new, src in sorted(ts + evs,
                                                     key=lambda x: x[:3]):
            if rank != 1:
                bal[(src, sym)] = bal.get((src, sym), 0.0) + q
                if not src:
                    mixed.add(sym)
                keys = [(src, sym)]
            else:
                keys = []
                for k in [k for k in bal if k[1] == sym]:
                    moved = bal.pop(k) * q
                    nk = (k[0], new)
                    bal[nk] = bal.get(nk, 0.0) + moved
                    keys += [k, nk]
                if not keys:
                    continue
                if sym in mixed:
                    mixed.add(new)
            for s in dict.fromkeys(k[1] for k in keys):
                out.setdefault((acct, s), []).append(
                    (d, sum(v for k, v in bal.items() if k[1] == s)))
            for k in dict.fromkeys(keys):
                if k[0]:
                    by_src.setdefault((acct, k[0], k[1]), []).append(
                        (d, bal.get(k, 0.0)))
            if rank == 1 and new != sym:
                mine = out[(acct, sym)]
                carried.setdefault(new, []).extend(
                    carried.pop(sym, []) + [mine[cut.get(sym, 0):]])
                cut[sym] = len(mine)
        for k in [k for k in by_src if k[0] == acct and k[2] in mixed]:
            del by_src[k]
        for (a, s), series in [i for i in out.items() if i[0][0] == acct]:
            own = series[cut.get(s, 0):]
            parts[(a, s)] = carried.get(s, []) + ([own] if own else [])
    return out, by_src, parts


def _held_now(parts: Dict[Tuple[str, str], List[List[Tuple[str, float]]]],
              events: List[Tuple[str, str, str, float]],
              as_of: Optional[str] = None
              ) -> Dict[str, List[Tuple[str, List[Tuple[str, float]]]]]:
    """`_held`'s balances per security as it is named now: {symbol:
    [(account, a (date, balance) list)]}: `_held`'s parts, the lists cut
    at each rename it walked (the old symbol's list to the rename, which
    ends at 0 on its date, beside the list the shares moved to; the old
    symbol's entries after it another security's, a ticker reused:
    backlog TJ-5052), and a rename of `events` (the splits, sorted:
    date, symbol, new symbol, ratio) after `as_of`, which `_held` did
    not walk, carrying a symbol's parts to the new one."""
    out: Dict[str, List[Tuple[str, List[Tuple[str, float]]]]] = {}
    for (acct, sym), pieces in parts.items():
        for d, old, new, _ratio in events:
            if as_of is not None and d > as_of and old == sym:
                sym = new
        for piece in pieces:
            out.setdefault(sym, []).append((acct, piece))
    return out


def _paid_and_ends(inc: Dict[tuple, Dict[str, Any]],
                   rows: List[Tuple[str, Dict[str, Any]]]
                   ) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """({symbol (now): the dates of `payments`' income entries `inc`
    with money left after the reversals: a row the schedule leaves out
    (no share count, another currency) is still a payment made},
    {account: the date of its last row of `rows`: where its books end,
    as far as the rows can say. Every row, whatever date the estimate
    is as of: how far the books reach is a fact about the books})."""
    paid: Dict[str, List[str]] = {}
    for k, e in inc.items():
        if e["gross"] > 0.005:
            paid.setdefault(e["sym"], []).append(k[3])
    ends: Dict[str, str] = {}
    for acct, t in rows:
        d = str(t.get("date") or "")[:10]
        if len(d) == 10:
            ends[acct] = max(ends.get(acct, ""), d)
    return paid, ends


def _said_no_count(d: str, e: Dict[str, Any],
                   notes: Dict[Tuple[str, str], List[str]]) -> bool:
    """Whether the payment `e` of `d` (`payments`: an entry with no share
    count) is one the parser marks as having none (`no_share_count`: no
    accrual in the broker's statements pays the row, so no neighbour's
    count is its own). It then borrows none: it is left out of the
    per-share figures, and `notes` says so."""
    if not e["row"].get("no_share_count"):
        return False
    notes.setdefault(e["key"][:1] + (e["sym"],), []).append(
        f"the {d} payment of {_money(e['gross'])} {e['currency']} has no "
        f"share count in the broker's statements: left out of the "
        f"per-share figures")
    return True


def payments(rows: Iterable[Tuple[str, Dict[str, Any]]],
             as_of: Optional[str] = None) -> "Payments":
    """{symbol: [Payment] by pay date} from (account, native row) pairs
    dated up to `as_of` (all of them when None):
    DIVIDEND (and DIVIDEND_IN_LIEU: the same stream, paid while the
    shares were lent) rows netted per account, broker account (the
    rows' source_account: two broker accounts in one taxjson account
    are two holdings, not one payment of both amounts on the larger
    share count), symbol, pay date and currency (a reversal and its
    re-posting are one payment; a CAD and a USD posting of one date are
    two, never one sum: 2026-10 audit A10), their withholding from the
    TAX rows of that account, broker account, symbol and date. The
    share count of a date is the largest count its DIVIDEND rows name
    (a special beside the regular dividend is on the same shares) plus
    the largest its DIVIDEND_IN_LIEU rows name (the lent shares are
    the others: a dividend on 5 and a payment in lieu on 5 is one
    payment on 10, audit B15). A SPLIT row dated after a payment's
    record (or ex-dividend, else pay) date scales its share count by
    the ratio, so every per-share amount is in today's denomination (a
    2-for-1 split doubled the forecast, audit A09; a dividend paid
    after the split on a record date before it, backlog TJ-0549);
    every SPLIT in the books, `as_of` or not: the shares the estimate
    multiplies by are the books' current holdings (a --date before the
    split doubled it again, 2026-10-25 audit planning-us#2). A rename
    (a SPLIT naming a new symbol) carries the payments before it to
    the new symbol (backlog TJ-0547: `_now`). A reversal posted on a
    later date nets against the latest earlier positive posting of
    that account, broker account, symbol and currency (the original it
    cancels), its TAX rows with it, so a correction and its refunded
    withholding count once (backlog TJ-0552). A TAX row in another
    currency than its income is kept in its own (`Payment.tax_fx`,
    backlog TJ-0548). A negative row is a dividend charged on a short
    position when the books put the account (the row's broker account,
    when its own trades are all the account holds of the symbol:
    backlog TJ-0558) short the symbol on the row's record (or pay) date
    (its trade rows, scaled by the splits and carried through a
    rename: `_held`; 2026-10-25 audit
    planning-us#3): an expense, neither a payment nor a correction
    (audit A50), whatever the row's own share count (Questrade's
    charge names "ON 100 SHS": planning-us#4); long by the books, it
    is a reversal (IB back-computes a negative count from the signed
    amount); with no trade row of the symbol in that account, a
    negative count decides (a short's expense) and `notes` says so,
    a positive one is a reversal. A positive row while the books put
    the account short (the same test) still counts as a payment, but
    the books likely miss a trade: `notes` and `short_paid` say so,
    once per account and symbol (backlog TJ-0073). A corporate
    action's deemed dividend
    (a spin-off election) is not part of a payer's schedule; a row
    with no share count (the broker's description names none) takes
    the count of the account's nearest dated payment of that symbol
    that has one, or is left out when none has."""
    rows = [(a, t) for a, t in rows if isinstance(t, dict)]

    def _ok(t: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
        act = str(t.get("action") or "").upper()
        sym = str(t.get("symbol") or "").strip()
        d = str(t.get("date") or "")[:10]
        if not sym or len(d) != 10:
            return None
        if as_of is not None and d > as_of:
            return None
        return act, sym, d
    # The splits: one corporate event per security, whichever account's
    # book posted it (split_copies, the one dedup rule: CA-CORP-12; two
    # books' copies a few days apart are one). Every split in the books
    # scales the payments before it, whatever `as_of`: the shares
    # `estimate` multiplies by are the books' current holdings, so the
    # history is read in the denomination after the last split (a
    # --date before a 2-for-1 split doubled the forecast: 2026-10-25
    # audit planning-us#2).
    from taxjson.lib.corporate_timeline import split_copies
    dup = split_copies([t for _a, t in rows])
    splits: List[Tuple[str, str, str, float]] = []    # date, sym, new, ratio
    for _a, t in rows:
        if str(t.get("action") or "").upper() != "SPLIT" or id(t) in dup:
            continue
        sym = str(t.get("symbol") or "").strip()
        d = str(t.get("date") or "")[:10]
        try:
            ratio = float(t.get("quantity") or 0.0)
        except (TypeError, ValueError):
            ratio = 0.0
        if sym and len(d) == 10 and ratio > 0:
            splits.append((d, sym, str(t.get("symbol_new") or "").strip()
                           or sym, ratio))
    held, held_src, parts = _held(rows, _ok, [e for e in splits
                                              if as_of is None or e[0] <= as_of])

    def _short_on(acct: str, src: str, sym: str,
                  when: str) -> Optional[bool]:
        """Whether `acct` was short `sym` at the end of `when` by the
        books (its trade rows, the splits, a rename carrying its shares
        in); None when no row of the books moves the symbol in it. The
        broker account `src` of a combined account by its own trades
        when they are all it holds of the symbol (one broker account's
        short beside another's larger long: backlog TJ-0558), else the
        account's."""
        hs = (held_src.get((acct, src, sym)) if src else None) \
            or held.get((acct, sym))
        if not hs:
            return None
        bal = 0.0
        for d, b in hs:
            if d > when:
                break
            bal = b
        return bal < -1e-9
    # The corporate events in date order: a payment's security now (a
    # rename carries its history to the new symbol: backlog TJ-0547)
    # and the factor that reads its share count in today's shares.
    events = sorted(splits)

    def _now(sym: str, pay: str, ent: str) -> Tuple[str, float]:
        """(the symbol a payment of `sym` paid on `pay`, its shares
        counted on `ent` (its record or ex-dividend date, else `pay`),
        has now, the factor to today's shares). An event after the
        entitlement date scales the count (the row names the shares of
        that date: a July dividend of a June record date before a June
        30 split is on the pre-split shares, backlog TJ-0549); a rename
        on or after the pay date carries the payment to the new symbol;
        a row of the old symbol paid and entitled after the rename is
        another security's, left where it is."""
        f = 1.0
        for sd, old, new, ratio in events:
            if old != sym:
                continue
            if ent < sd:
                f *= ratio
            elif pay > sd:
                continue
            sym = new
        return sym, f
    inc: Dict[tuple, Dict[str, Any]] = {}
    tax: Dict[tuple, float] = {}
    # Withholding in another currency than the income it belongs to:
    # {income key: {currency: amount}} (backlog TJ-0548).
    tax_fx: Dict[tuple, Dict[str, float]] = {}
    notes: Dict[Tuple[str, str], List[str]] = {}
    short_paid: Dict[Tuple[str, str], List[str]] = {}
    short_now: Dict[Tuple[str, str], List[str]] = {}
    for acct, t in rows:
        key = _ok(t)
        if key is None:
            continue
        act, sym, d = key
        cur = str(t.get("currency") or "").upper()
        src = str(t.get("source_account") or "")
        k = (acct, src, sym, d, cur)
        if act == "TAX" or str(t.get("type") or "").lower() == "tax":
            tax[k] = tax.get(k, 0.0) + float(t.get("net_amount") or 0.0)
            continue
        if act not in _INCOME:
            continue
        if t.get("corp_election") or t.get("corp_event_id"):
            continue
        amt = (float(t.get("gross_amount") or 0.0)
               or float(t.get("net_amount") or 0.0))
        qraw = float(t.get("quantity") or 0.0)
        # Short or long on the record (or pay) date by the books,
        # whatever the row's own count says: Questrade's charge names
        # "ON 100 SHS", a positive count (2026-10-25 audit
        # planning-us#4).
        when = d
        for f in ("record_date", "ex_date"):
            rd = str(t.get(f) or "")[:10]
            if len(rd) == 10 and rd <= d:
                when = min(when, rd)
        if amt > 0.005 and _short_on(acct, src, sym, when):
            # Paid while the books put the account short: still income
            # (the broker paid it), but a short is charged a dividend,
            # not paid one: the books likely miss a trade before it
            # (backlog TJ-0073).
            short_paid.setdefault((acct, sym), []).append(d)
            short_now.setdefault((acct, _now(sym, d, when)[0]),
                                 []).append(d)
        if amt < -0.005:
            short = _short_on(acct, src, sym, when)
            if short is None and qraw < 0:
                notes.setdefault((acct, _now(sym, d, when)[0]), []).append(
                    f"the {d} row of {_money(amt)} {cur} on {qraw:g} "
                    f"shares was read as a short position's dividend "
                    f"expense, not a correction of an earlier payment: "
                    f"{acct} has no trade of {sym} in the books to say")
                continue
            if short:
                continue
        e = inc.setdefault(k, {"gross": 0.0, "qty": 0.0, "qty_by": {},
                               "row": t, "row_qty": -1.0, "currency": cur,
                               "when": when, "key": k})
        e["gross"] += amt
        q = abs(qraw)
        if q > e["qty_by"].get(act, 0.0):
            e["qty_by"][act] = q
        e["qty"] = sum(e["qty_by"].values())
        if q > e["row_qty"]:
            e["row_qty"], e["row"], e["when"] = q, t, when
    # Each payment's security now and its share count in today's
    # denomination (the splits and renames after its entitlement date).
    for (acct, src, sym, d, cur), e in inc.items():
        e["sym"], f = _now(sym, d, e["when"])
        e["qty"] *= f
    # A TAX row naming no broker account (or another one, or another
    # currency) on a date with no income of its own: the withholding of
    # that date's matching, else largest, holding in the account; in
    # another currency than that holding's, kept in its own (converted
    # by `estimate` at the pay date's rate: backlog TJ-0548).
    for k in list(tax):
        if k in inc:
            continue
        acct, src, sym, d, cur = k
        same = [j for j in inc if j[0] == acct and j[2:4] == (sym, d)]
        if same:
            j = max(same, key=lambda j: (j[1] == src, j[4] == cur,
                                         inc[j]["qty"]))
            if j[4] == cur:
                tax[j] = tax.get(j, 0.0) + tax.pop(k)
            else:
                fx = tax_fx.setdefault(j, {})
                fx[cur] = fx.get(cur, 0.0) + tax.pop(k)
    # Per account, symbol (now) and currency, in date order: a
    # net-negative date (a reversal) cancels the latest earlier positive
    # posting(s) of its broker account (any, when one of the two names
    # none), its TAX rows (a refund of the withholding) moving with it
    # in proportion (a correction of 10 to 5 with half its tax refunded
    # left the whole tax on the 5: backlog TJ-0552); a date with no
    # share count borrows the nearest dated one.
    by_pair: Dict[tuple, List[tuple]] = {}
    for (acct, src, sym, d, cur), e in inc.items():
        by_pair.setdefault((acct, e["sym"], cur), []).append((d, src, e))

    def _same_src(a: str, b: str) -> bool:
        return not a or not b or a == b
    for ents in by_pair.values():
        ents.sort(key=lambda x: x[0])
        for i, (d, src, e) in enumerate(ents):
            if e["gross"] >= -0.005:
                continue
            left = whole = -e["gross"]
            e["gross"] = 0.0
            rtax = tax.pop(e["key"], 0.0)
            rfx = tax_fx.pop(e["key"], {})
            for _pd, psrc, pe in reversed([x for j, x in enumerate(ents)
                                           if j != i and x[0] <= d]):
                if pe["gross"] <= 0.005 or not _same_src(src, psrc):
                    continue
                take = min(left, pe["gross"])
                pe["gross"] -= take
                left -= take
                part = take / whole
                pk = pe["key"]
                if rtax:
                    tax[pk] = tax.get(pk, 0.0) + rtax * part
                for c, a in rfx.items():
                    fx = tax_fx.setdefault(pk, {})
                    fx[c] = fx.get(c, 0.0) + a * part
                if left <= 0.005:
                    break
        for i, (d, src, e) in enumerate(ents):
            if (e["qty"] > 1e-9 or e["gross"] <= 0.005
                    or _said_no_count(d, e, notes)):
                continue
            near = [(abs((date.fromisoformat(d) - date.fromisoformat(pd)).days), pe["qty"])
                    for pd, psrc, pe in ents
                    if pe["qty"] > 1e-9 and _same_src(src, psrc)]
            if near:
                e["qty"] = min(near)[1]
    for (acct, sym), ds in short_now.items():
        ds.sort()
        notes.setdefault((acct, sym), []).append(
            (f"the {ds[0]} payment" if len(ds) == 1
             else f"{len(ds)} payments from {ds[0]}")
            + f" arrived while the books show {acct} short {sym}: "
            f"counted as income; the books likely miss a trade (tjs "
            f"find-missing-history)")
    for ds in short_paid.values():
        ds.sort()
    out = Payments()
    out.rows, out.notes, out.short_paid = rows, notes, short_paid
    out.held = _held_now(parts, events, as_of)
    out.paid, out.ends = _paid_and_ends(inc, rows)
    for k, e in inc.items():
        acct, src, sym, d, cur = k
        if e["gross"] <= 0.005 or e["qty"] <= 1e-9:
            continue
        row = e["row"]
        if e["sym"] != sym:
            # Renamed since: the projection reads the row as the
            # security's now (its dating rule, ric_january_dividends).
            row = dict(row, symbol=e["sym"])
        out.setdefault(e["sym"], []).append(Payment(
            pay_date=d, per_share=e["gross"] / e["qty"], gross=e["gross"],
            qty=e["qty"], currency=cur, account=acct,
            tax=max(0.0, tax.get(k, 0.0)), row=row, source=src,
            tax_fx={c: a for c, a in (tax_fx.get(k) or {}).items()
                    if abs(a) > 1e-9}))
    for ps in out.values():
        ps.sort(key=lambda p: (p.pay_date, -p.qty))
    return out


# Postings of one payment a few days apart (two brokers' pay dates, a
# special dividend paid beside the regular one) are one payment.
CLUSTER_DAYS = 5
# A gap matches k payments of a period P when it is within
# GAP_TOL * P (+ GAP_TOL_K * k * P for a long gap) of k * P.
GAP_TOL, GAP_TOL_K = 0.25, 0.05
# A payment on the payer's own day: within this share of a period of one
# period after the one before (a posting's drift, not a special's week).
DAY_TOL = 0.1


def schedule(pays: List[Payment]) -> List[Payment]:
    """One payment per pay date across accounts: postings within
    CLUSTER_DAYS of each other are one payment, its per-share amount
    the largest holding's (an account's two rows of it, a special and a
    regular dividend, added; each broker account of a combined taxjson
    account a holding of its own, and a holding's postings in another
    currency another holding: amounts in unlike currencies are never
    added). A posting more than CLUSTER_DAYS after an earlier one of its
    own holding in the cluster starts the next payment: a weekly payer
    posted a few days apart by two brokers is not one chain of every
    week's postings."""
    out: List[Payment] = []
    group: List[Payment] = []

    def _flush():
        if not group:
            return
        by: Dict[tuple, List[Payment]] = {}
        for p in group:
            by.setdefault((p.account, p.source, p.currency), []).append(p)
        best = None
        for (acct, src, _cur), ps in by.items():
            qty = max(p.qty for p in ps)
            per = sum(p.gross for p in ps) / qty if qty else 0.0
            last = max(ps, key=lambda p: (p.pay_date, p.qty))
            tfx: Dict[str, float] = {}
            for p in ps:
                for c, a in p.tax_fx.items():
                    tfx[c] = tfx.get(c, 0.0) + a
            cand = Payment(pay_date=last.pay_date, per_share=per,
                           gross=sum(p.gross for p in ps), qty=qty,
                           currency=last.currency, account=acct,
                           tax=sum(p.tax for p in ps), row=last.row,
                           source=src, tax_fx=tfx)
            if best is None or cand.qty > best.qty:
                best = cand
        out.append(best)
        group.clear()
    def _days(a: Payment, b: Payment) -> int:
        return (date.fromisoformat(b.pay_date)
                - date.fromisoformat(a.pay_date)).days
    for p in sorted(pays, key=lambda p: p.pay_date):
        if group and (_days(group[-1], p) > CLUSTER_DAYS
                      or any(_days(q, p) > CLUSTER_DAYS for q in group
                             if (q.account, q.source)
                             == (p.account, p.source))):
            _flush()
        group.append(p)
    _flush()
    return out


def _slot(anchor: date, months: int, days: int, k: int) -> date:
    """The grid date k periods before `anchor`."""
    if months:
        return date.fromisoformat(add_months(anchor.isoformat(), -months * k))
    return anchor - timedelta(days=days * k)


def detect_frequency(dates: List[str]) -> Tuple[Optional[str],
                                                Optional[int],
                                                Optional[float]]:
    """(name, months between payments (0: weekly), typical gap in days)
    from sorted pay dates (detect_schedule without its anchor)."""
    return detect_schedule(dates)[:3]


def detect_schedule(dates: List[str]) -> Tuple[Optional[str],
                                               Optional[int],
                                               Optional[float],
                                               Optional[str]]:
    """(name, months between payments (0: weekly), typical gap in days,
    the latest payment on the grid) from sorted pay dates; (None, None,
    gap, None) when irregular, (None, None, None, None) with fewer than
    two payments. Each frequency's grid is anchored at one of the last
    LOOKBACK payments: a payment is on the grid when it is within
    GAP_TOL of a period from its slot. A frequency fits when two thirds
    of the payments are on its grid, no slot holds more than two (a
    payment and a special, or a second broker's posting of it) and two
    payments fill adjacent slots (a stretch of empty slots is the
    position not held; a payment off the grid is a special); the one
    with the most adjacent slots filled wins, then the most slots, then
    the shortest period (a quarterly payer's dates are on the monthly
    grid too, but fill no adjacent monthly slots; they are on the
    weekly grid as well, 13 weeks to a quarter, so weekly needs two
    thirds of its slot gaps adjacent), then the latest anchor. Only the
    latest payment anchored the grids: an off-cycle special paid last
    put every regular monthly payment off the monthly grid, and a
    coarser grid through the special won (backlog TJ-0900). Two or more
    payments after the winner's latest that fit a grid of their own are
    a change of schedule, not specials: the later regime wins, whatever
    the older one's run of slots (backlog TJ-1078). The latest on the
    grid is not a special that slot 0 also holds (_latest_regular)."""
    ds = [date.fromisoformat(d) for d in dates[-LOOKBACK:]]
    if len(ds) < 2:
        return None, None, None, None
    best = _best_grid(ds)
    if best is None:
        gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
        return None, None, float(median(gaps)), None
    after = [d for d in ds if d.isoformat() > best[4]]
    if len(after) >= 2:
        later = _best_grid(after)
        # Two payments a week apart are two specials, not a weekly payer.
        if later and (later[2] or len(after) >= 3):
            best = later
    return best[1], best[2], best[3], best[4]


def _best_grid(ds: List[date]) -> Optional[tuple]:
    """The winning (key, name, months, gap, latest on the grid) for the
    sorted dates `ds` (detect_schedule's rule), or None."""
    best = None
    for anchor in sorted(set(ds)):
        for name, months, days in FREQUENCIES:
            period = months * 365.25 / 12 if months else float(days)
            tol = period * GAP_TOL
            slots: Dict[int, List[date]] = {}
            for d in ds:
                k = max(0, int(round((anchor - d).days / period)))
                if abs((d - _slot(anchor, months, days, k)).days) <= tol:
                    slots.setdefault(k, []).append(d)
            # A slot holds a payment and at most one more (a special, a
            # second broker's posting): three is a shorter period.
            if any(len(v) > 2 for v in slots.values()):
                continue
            on = sum(len(v) for v in slots.values())
            ks = sorted(slots)
            adj = [(slots[a][-1] - slots[b][-1]).days
                   for a, b in zip(ks, ks[1:]) if b == a + 1]
            if not adj or on * 3 < 2 * len(ds):
                continue
            # Every grid is on the weekly one (13 weeks to a quarter):
            # the weekly payer is the one whose slots are mostly
            # adjacent.
            if not months and len(adj) * 3 < 2 * (len(ks) - 1):
                continue
            key = (len(adj), len(slots), -period, anchor)
            if best is None or key > best[0]:
                # The latest payment on the grid (slot 0 may hold a
                # posting a few days after the anchor).
                best = (key, name, months, float(median(adj)),
                        _latest_regular(slots, months, period).isoformat()
                        if 0 in slots else anchor.isoformat())
    return best


def _latest_regular(slots: Dict[int, List[date]], months: int,
                    period: float) -> date:
    """The latest payment of a grid's slots. Two in slot 0 are a payment
    and a special, though the grid's tolerance holds both (backlog
    TJ-1155): the payment is the one on the payer's own day, a period
    after slot 1's; with neither there, and slots 1 and 2 a period apart,
    both are specials and slot 1's is the latest. A shape that says less
    keeps the last of the slot."""
    top = max(slots[0])
    prev = slots.get(1) or []
    if len(slots[0]) != 2 or not months or len(prev) != 1:
        return top
    tol = period * DAY_TOL

    def _next(d: date) -> date:
        return date.fromisoformat(add_months(d.isoformat(), months))
    near = [d for d in slots[0] if abs((d - _next(prev[0])).days) <= tol]
    if len(near) == 1:
        return near[0]
    older = slots.get(2) or []
    if (not near and len(older) == 1
            and abs((_next(older[0]) - prev[0]).days) <= tol):
        return prev[0]
    return top


# ---------------------------------------------- the assumed schedule
# A payer with at most this many payments in the books can be read as
# quarterly on assumption.
ASSUME_MAX = 3
ASSUMED = "quarterly"
# The shares were held on a quarterly date when the books show them
# long on every day from HELD_LEAD of a quarter before it (a record
# date is weeks before its pay date, and the pay date can be early by
# the grid's tolerance) through the date itself: a holder long that far
# was entitled, whatever was sold after.
HELD_LEAD = 0.5
# Days with no shares in any account that still count as held: a
# transfer between the owner's accounts leaves one book days before it
# reaches the other (out on the 20th, in on the 23rd).
TRANSIT_DAYS = 5
_QUARTER = 3 * 365.25 / 12


def _long_through(series: List[List[Tuple[str, float]]], lo: str,
                  hi: str) -> bool:
    """Whether the balances `series` (`_held`'s lists of (date, signed
    balance after it), by date: every account's, and a renamed
    security's old and new name) add up to a long position at the end
    of every day from `lo` to `hi`: each list counts when positive (one
    account's short is not another's holding), so the test follows the
    position through a rename and a move between accounts. Up to
    TRANSIT_DAYS with nothing held, between days that are long, still
    count."""
    gap = None
    for day in sorted({lo} | {d for s in series for d, _q in s
                              if lo < d <= hi}):
        total = 0.0
        for s in series:
            q = 0.0
            for d, after in s:
                if d > day:
                    break
                q = after
            total += max(q, 0.0)
        if total <= 1e-9:
            gap = gap or day
            continue
        if gap and (gap == lo or (date.fromisoformat(day)
                                  - date.fromisoformat(gap)).days
                    > TRANSIT_DAYS):
            return False
        gap = None
    return bool(series) and gap is None


def _date_reading(pays: Any, sym: str, slot: date, as_of: str) -> str:
    """What the books say of the quarterly date `slot` of `sym`, a date
    the schedule has no payment on:

    "paid"    an income row of the security is within the grid's
              tolerance of it, in any currency, with or without a share
              count (`Payments.paid`; the payments of every currency
              when `pays` is a plain dict): the schedule left the row
              out, the payer did not skip the date. (A return of
              capital booked as an adjustment is no income row.)
    "held"    the accounts whose books run to the quarterly grace past
              the date show, together, the shares long from HELD_LEAD
              of a quarter before it through it (`_long_through`): the
              payer skipped it;
    "end:ACCOUNT"  all the accounts together show them long so, but not
              without ACCOUNT, whose books end before the grace past
              the date (`Payments.ends`, its last row): they cannot say
              whether it was paid. Also an account that was paid the
              security, where no trade row of it is in the books, and
              that ends so;
    "unheld"  trade rows of the security, and no position long so: no
              payment was due;
    ""        no trade row of the security, in books that reach the
              date: nothing explains the gap."""
    tol = timedelta(days=int(_QUARTER * GAP_TOL))
    every = pays.get(sym) or []
    dates = (getattr(pays, "paid", None) or {}).get(sym) \
        or [p.pay_date for p in every]
    if any(_iso(slot - tol) <= d <= min(_iso(slot + tol), as_of)
           for d in dates):
        return "paid"
    series = (getattr(pays, "held", None) or {}).get(sym) or []
    ends = getattr(pays, "ends", None) or {}
    reach = _iso(slot + timedelta(days=GRACE_DAYS[ASSUMED]))
    short = sorted(a for a in {a for a, _b in series}
                   | ({p.account for p in every} if not series else set())
                   if ends.get(a, reach) < reach)
    if not series:
        return "end:" + short[0] if short else ""
    try:
        lo = _iso(slot - timedelta(days=int(_QUARTER * HELD_LEAD)))
    except OverflowError:
        return "unheld"             # before 0001-01-01: nothing held
    hi = min(_iso(slot), as_of)
    if _long_through([b for a, b in series if a not in short], lo, hi):
        return "held"
    if short and _long_through([b for _a, b in series], lo, hi):
        return "end:" + next(
            (a for a in short if not _long_through(
                [b for x, b in series if x != a], lo, hi)), short[0])
    return "unheld"


def assumed_quarterly(sched: List[Payment], name: Optional[str],
                      pays: Any, sym: str, as_of: str
                      ) -> Tuple[bool, set, List[str]]:
    """(whether the payer `sym` of `sched` (its payments, `schedule`) is
    projected quarterly on assumption, the quarterly dates since its
    latest payment on which none was due or the books cannot say, the
    row's check lines). Assumed: its own history cannot settle its
    schedule, and nothing in the books contradicts quarterly (most
    payers of shares are; one payment a year, the pro rata reading of a
    single payment, is the worse guess: the owner's request of
    2026-11-22, backlog TJ-4976). `name`: detect_schedule's reading;
    `pays`: the `Payments` (a plain dict: nothing known of holdings).

    Cannot settle: at most ASSUME_MAX payments, the latest in the 12
    months to `as_of`, each alone in its slot of the quarterly grid
    anchored at the latest (the detector's tolerance, GAP_TOL of a
    quarter), where the detector found no grid or a semi-annual one
    that rests on a single six-month gap. A grid the detector did read
    (quarterly or shorter, annual, three payments six months apart) is
    settled: never touched here. Two payments closer than a quarter, or
    one off the quarterly grid, contradict it by the dates alone.

    Contradicted (the reading is then the detector's, as before), by a
    quarterly date the schedule has no payment on (`_date_reading`):
    - between the payments, a date the books show as held;
    - after the latest payment and more than the quarterly grace
      overdue on `as_of`: a date the books show as held; one that
      nothing explains (no trade row of the security, in books that
      reach it); or a second date past the end of an account's books.

    An overdue date that was paid by a row the schedule leaves out, or
    that the books show as not held, is no missing payment: it is in
    the set returned. One past the end of an account's books is
    unchecked, and not free: it is not in the set, so `estimate` counts
    it as a settled payer's missing payment (one: late, still
    projected), and two drop the assumption. Either way the check lines
    name the account and where its books end, assumed or not."""
    no: Tuple[bool, set, List[str]] = (False, set(), [])
    if len(sched) > ASSUME_MAX or name not in (None, "semi-annual"):
        return no
    ds = [date.fromisoformat(p.pay_date) for p in sched]
    today = date.fromisoformat(as_of)
    anchor, tol = ds[-1], _QUARTER * GAP_TOL
    if (today - anchor).days >= 365:
        return no                   # no payment in the last 12 months
    filled = set()
    for d in ds:
        k = int((anchor - d).days / _QUARTER + 0.5)
        if k in filled or abs((d - _slot(anchor, 3, 91, k)).days) > tol:
            return no
        filled.add(k)
    half = 2 * _QUARTER
    if sum(1 for a, b in zip(ds, ds[1:])
           if abs((b - a).days - half) <= half * GAP_TOL) >= 2:
        return no                   # three payments six months apart
    if any(_date_reading(pays, sym, _slot(anchor, 3, 91, k), as_of)
           == "held" for k in range(1, max(filled)) if k not in filled):
        return no
    excused, cut, ends = set(), [], getattr(pays, "ends", None) or {}
    for k in range(1, 5):
        try:
            nxt = date.fromisoformat(add_months(_iso(anchor), 3 * k))
        except (ValueError, OverflowError):
            break                   # past 9999-12-31: nothing overdue
        if (today - nxt).days <= GRACE_DAYS[ASSUMED]:
            break
        said = _date_reading(pays, sym, nxt, as_of)
        if said in ("held", ""):
            return no
        if said.startswith("end:"):
            cut.append(said[4:])
        else:
            excused.add(_iso(nxt))
    why = [f"the books of {a} end {ends[a]}: later dates not checked"
           for a in dict.fromkeys(cut)]
    if len(cut) >= 2:
        return False, set(), why
    return True, excused, [_assumed_flag(len(sched))] + why


def _assumed_flag(n: int) -> str:
    """The check line of a payer projected quarterly on assumption."""
    return (f"assumed quarterly: {n} payment(s) in the books, none "
            f"contradicts it (over-projected if it pays less often"
            + (", or if its only payment was a special)" if n == 1
               else ")"))


def _on_schedule(sched: List[Payment], name: Optional[str],
                 on_grid: Optional[str], flags: List[str]) -> Payment:
    """The payment the forecast runs from: the latest ON the schedule.
    A later one off it (a special paid last) is neither the next date's
    base nor its amount, and `flags` names it (backlog TJ-0900)."""
    latest = sched[-1]
    if not (name and on_grid and on_grid < latest.pay_date):
        return latest
    base_pay = [p for p in sched if p.pay_date <= on_grid][-1]
    off = [p.pay_date for p in sched if p.pay_date > on_grid]
    flags.append(f"{len(off)} payment(s) off the {name} schedule "
                 f"after {base_pay.pay_date} (a special?): "
                 f"{', '.join(off)}, left out of the forecast")
    return base_pay


def ttm_per_share(sched: List[Payment], as_of: str) -> float:
    lo = _iso(date.fromisoformat(as_of) - timedelta(days=365))
    return sum(p.per_share for p in sched if lo < p.pay_date <= as_of)


def _projected_tax_date(latest: Payment, pay: str,
                        income_date: Optional[Callable]) -> str:
    """The tax date of a payment projected to `pay`: the latest real
    row moved there (its record date moved by the same lead) and dated
    by the project's income rule."""
    if income_date is None:
        return pay
    row = dict(latest.row)
    rec = str(row.get("record_date") or "")[:10]
    if rec:
        try:
            lead = (date.fromisoformat(latest.pay_date)
                    - date.fromisoformat(rec)).days
            row["record_date"] = _iso(date.fromisoformat(pay)
                                      - timedelta(days=lead))
        except ValueError:
            row.pop("record_date", None)
    row["date"] = pay
    row["date_settle"] = pay
    try:
        return str(income_date(row) or pay)
    except Exception:                                      # noqa: BLE001
        return pay


def estimate(positions: Iterable[Dict[str, Any]],
             pays: Dict[str, List[Payment]], *, as_of: str, months: int,
             tax_year: int, base: str,
             fx: Callable[[str], Optional[float]],
             income_date: Optional[Callable] = None,
             fx_on: Optional[Callable[[str, str], Optional[float]]] = None
             ) -> Dict[str, Any]:
    """The estimate document. `positions`: {account, group ("taxable" |
    "sheltered"), symbol, qty} held now (long shares only); `fx(cur)`:
    the base-currency rate of `cur` (None: no rate in the books);
    `fx_on(cur, date)`: its rate on a date (a withholding posted in
    another currency than its dividend is compared at the pay date's
    rates; `fx` when not given or it has none).
    ValueError (`horizon_problem`) when the window or the tax year
    leaves the calendar."""
    bad = horizon_problem(as_of, months, tax_year)
    if bad:
        raise ValueError(bad)

    def _rate_on(cur: str, on: str) -> Optional[float]:
        if cur == base.upper():
            return 1.0
        r = fx_on(cur, on) if fx_on is not None else None
        return r if r else fx(cur)
    horizon = add_months(as_of, months)
    ystart, yend = f"{tax_year}-01-01", f"{tax_year}-12-31"
    if isinstance(pays, Payments) and pays.rows:
        pays = payments(pays.rows, as_of=as_of)
    notes = getattr(pays, "notes", None) or {}
    # The projection runs to the later of the window and the tax year
    # (plus the month a December-record distribution is paid in): the
    # rest-of-year total is the year's, whatever window was asked for
    # (2026-10 audit R08).
    reach = max(horizon, add_months(yend, 1))
    rows: List[Dict[str, Any]] = []
    omitted: List[Dict[str, Any]] = []
    for pos in sorted(positions, key=lambda p: (p["symbol"],
                                                p["account"])):
        sym, acct = pos["symbol"], pos["account"]
        # The history as of the date (a later payment in the books is
        # not known yet on it).
        every = [p for p in (pays.get(sym) or []) if p.pay_date <= as_of]
        if not every:
            omitted.append({"account": acct, "symbol": sym,
                            "shares": pos["qty"]})
            continue
        flags: List[str] = list(notes.get((acct, sym), []))
        # One currency: the one this account's latest payment (else the
        # payer's) was paid in. Another broker's postings in another
        # currency are another holding's, flagged and left out (audit
        # A10: a USD and a CAD posting of one date were summed).
        own = [p for p in every if p.account == acct] or every
        cur = max(own, key=lambda p: (p.pay_date, p.qty)).currency
        hist = [p for p in every if p.currency == cur]
        others = sorted({p.currency for p in every if p.currency != cur})
        if others:
            flags.append(f"payments in {', '.join(others)} too: estimated "
                         f"from the {cur} ones")
        sched = schedule(hist)
        latest = sched[-1]
        name, step, gap, on_grid = detect_schedule(
            [p.pay_date for p in sched])
        # Too short a history to settle a schedule: quarterly, shown as
        # assumed, until the books contradict it (`why`: its check
        # lines). `excused`: the dates since on which no payment was due.
        assumed, excused, why = assumed_quarterly(sched, name, pays, sym,
                                                  as_of)
        flags += why
        if assumed:
            name, step, on_grid = ASSUMED, 3, latest.pay_date
        base_pay = _on_schedule(sched, name, on_grid, flags)
        ttm = ttm_per_share(sched, as_of)
        rate = 1.0 if cur == base.upper() else fx(cur)
        # The withholding rate: this account's latest payment, every
        # broker account's posting of it together (its TAX over its
        # gross; the smallest holding's rate alone put a 20% rate on
        # the whole position: audit A29).
        mine = [p for p in hist if p.account == acct]
        wrate = None
        if mine:
            last_d = date.fromisoformat(mine[-1].pay_date)
            grp = [p for p in mine
                   if (last_d - date.fromisoformat(p.pay_date)).days
                   <= CLUSTER_DAYS]
            g, w = sum(p.gross for p in grp), sum(p.tax for p in grp)
            # Withholding posted in another currency: in the payment's
            # at the pay date's rates (CAD 2.70 over USD 10 is 20% at
            # 1.35, not 27%: backlog TJ-0548); no rate, no rate read.
            unconv: List[str] = []
            for p in grp:
                for c, a in p.tax_fx.items():
                    r_tax, r_pay = _rate_on(c, p.pay_date), \
                        _rate_on(cur, p.pay_date)
                    if r_tax is None or not r_pay:
                        unconv.append(c)
                    else:
                        w += a * r_tax / r_pay
            if unconv:
                flags.append(f"withholding in {', '.join(sorted(set(unconv)))}"
                             f" on {cur} payments: no rate in the books to "
                             f"compare them, the withholding is not "
                             f"estimated")
            elif w > 0.005 and g > 0:
                wrate = w / g
        shares = float(pos["qty"])
        per_pay = base_pay.per_share * shares
        projected: List[Dict[str, Any]] = []
        missed = 0
        status = "ok"
        win = roy = 0.0
        if name:
            grace = GRACE_DAYS[name]
            k = 1
            while True:
                try:
                    d = (add_months(base_pay.pay_date, step * k) if step
                         else _iso(date.fromisoformat(base_pay.pay_date)
                                   + timedelta(days=7 * k)))
                except (ValueError, OverflowError):
                    break               # past 9999-12-31: nothing to come
                if d > reach:
                    break
                k += 1
                if (date.fromisoformat(as_of)
                        - date.fromisoformat(d)).days > grace:
                    missed += d not in excused
                    continue
                td = _projected_tax_date(base_pay, d, income_date)
                projected.append({"pay_date": d, "tax_date": td,
                                  "amount": M.units(per_pay, 6)})
            if missed >= 2:
                status = "stopped"
                flags.append(f"{missed} expected payments not in the books")
                projected = []
            elif missed == 1:
                status = "late"
                flags.append("1 expected payment not in the books")
            roy = sum(p["amount"] for p in projected
                      if ystart <= p["tax_date"] <= yend)
            projected = [p for p in projected if p["pay_date"] <= horizon]
            win = sum(p["amount"] for p in projected)
        else:
            status = "irregular"
            flags.append("single payment" if len(sched) < 2
                         else "irregular schedule")
            if ttm <= 1e-12:
                status = "stopped"
                flags.append("no payment in the last 12 months")
            else:
                win = ttm * shares * months / 12.0
                start = max(date.fromisoformat(as_of),
                            date.fromisoformat(ystart) - timedelta(days=1))
                left = (date.fromisoformat(yend) - start).days
                roy = ttm * shares * max(0, left) / 365.0 if left > 0 \
                    else 0.0
        def _b(v):
            return None if rate is None else M.shown(v * rate, 2)
        nxt = projected[0] if projected else None
        rows.append({
            "account": acct, "group": pos.get("group") or "",
            "symbol": sym, "currency": cur, "shares": shares,
            "frequency": name or ("1 payment" if len(sched) < 2
                                  else "irregular"),
            "frequency_assumed": assumed,
            "median_gap_days": gap, "payments": len(sched),
            "last_pay_date": latest.pay_date,
            "last_per_share": M.shown(latest.per_share, 6),
            "ttm_per_share": M.shown(ttm, 6),
            "next_pay_date": nxt["pay_date"] if nxt else None,
            "next_tax_date": nxt["tax_date"] if nxt else None,
            "next_amount": M.shown(nxt["amount"], 2) if nxt else None,
            "next_amount_base": _b(nxt["amount"]) if nxt else None,
            "window_amount": M.shown(win, 2),
            "window_amount_base": _b(win),
            "rest_of_year_amount": M.shown(roy, 2),
            "rest_of_year_base": _b(roy),
            "fx_rate": rate,
            "withholding_rate": (M.units(wrate, 6) if wrate is not None
                                 else None),
            "withholding_window_base": (_b(win * wrate)
                                        if wrate is not None else None),
            "withholding_rest_of_year_base": (_b(roy * wrate)
                                              if wrate is not None
                                              else None),
            "status": status, "flags": flags,
            "projected": [dict(p, amount=M.shown(p["amount"], 2),
                               amount_base=_b(p["amount"]))
                          for p in projected],
        })

    def _tot(key):
        t = {"taxable": 0.0, "sheltered": 0.0}
        for r in rows:
            v = r.get(key)
            if v is not None and r["group"] in t:
                t[r["group"]] += v
        return {g: M.shown(v, 2) for g, v in t.items()}
    return {
        "schema_version": SCHEMA_VERSION, "planning_only": True,
        "as_of_date": as_of, "months": months, "window_end": horizon,
        "tax_year": tax_year, "currency": base.upper(),
        "rows": rows,
        "totals": {
            "rest_of_year": _tot("rest_of_year_base"),
            "window": _tot("window_amount_base"),
            "withholding_window": _tot("withholding_window_base"),
            "withholding_rest_of_year": _tot(
                "withholding_rest_of_year_base"),
        },
        "omitted_no_history": omitted,
        "no_fx": sorted({r["currency"] for r in rows
                         if r["fx_rate"] is None}),
    }


# ------------------------------------------------------------- render
def _few(names: List[str], n: int) -> str:
    names = list(dict.fromkeys(names))
    return ", ".join(names[:n]) + (f" +{len(names) - n} more"
                                   if len(names) > n else "")


def _ps(v: float) -> str:
    return f"{M.shown(v, 4):,.4f}".rstrip("0").rstrip(".") if v else "0"


def _assumed(r: Dict[str, Any]) -> bool:
    return bool(r.get("frequency_assumed"))


def _how(dating: str) -> List[str]:
    """HOW IT IS ESTIMATED's lines; `dating`: the project's own dating
    rule, in brackets."""
    return [
        "Frequency: the grid (weekly, monthly, quarterly, semi-annual, "
        "annual) most of the last payments fall on, anchored at the "
        "latest on it; a special or a stretch not held does not break "
        "it.",
        "Too few payments to tell (one to three, each on a quarterly date "
        "counted back from the latest, the latest in the last 12 months): "
        "quarterly, assumed (FREQ quarterly?), until the books contradict "
        "it: a quarterly date passed with the shares held and no payment "
        "of any kind in the books. A payer that pays less often, or a "
        "special as the only payment, is over-projected until then.",
        "No grid, and not assumed quarterly: pro rata from the 12-month "
        "amount per share.",
        "Each projected payment: the latest per-share amount x the shares "
        "held now, at the books' latest rate, in the tax year of its pay "
        f"date{dating}, as in divs-sum.",
        "A payment more than a few days overdue is missing from the books; "
        "two missing stop the projection. Planning numbers: a payer can "
        "change a dividend at any time."]


def next_last(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows in the order the text view lists them: by the next pay
    date, the latest first, so the payment that comes next is the bottom
    row; a row with no projected payment above them, by ticker. (`--json`
    keeps the document's order, by ticker and account.)"""
    undated = [r for r in rows if not r["next_pay_date"]]
    dated = sorted((r for r in rows if r["next_pay_date"]),
                   key=lambda r: (r["symbol"], r["account"]))
    dated.sort(key=lambda r: r["next_pay_date"], reverse=True)
    return undated + dated


def render(doc: Dict[str, Any], details: bool = False,
           cmd: str = "tjs div-estimate", country: Optional[str] = None):
    """The text view (lib/out.Doc): a legend, one row per position,
    the totals as figures, then what to check; --details adds each
    payer's schedule and projected payments and the omitted positions.
    `country`: the project's, for the dating rule HOW IT IS ESTIMATED
    names (CA-PLAN-06 / US-PLAN-07)."""
    from taxjson.lib import out
    from taxjson.lib.report_fmt import fmt_money
    base = doc["currency"]
    n = doc["months"]
    d = out.Doc(f"DIVIDEND ESTIMATE — planning numbers, as of "
                f"{doc['as_of_date']} ({base})")
    rows = doc["rows"]
    shown = next_last(rows)
    if rows:
        d.para("Planning numbers, not filing: last per-share amount x "
               "shares held, on the payer's own schedule. By NEXT pay "
               "date, the payment that comes next at the bottom."
               + (" FREQ quarterly?: assumed, too few payments in the "
                  "books to tell." if any(_assumed(r) for r in rows)
                  else ""))
        body = []
        for r in shown:
            body.append([
                r["symbol"], r["account"],
                r["frequency"] + ("?" if _assumed(r) else ""),
                f"{r['shares']:,.4f}".rstrip("0").rstrip("."),
                f"{_ps(r['last_per_share'])} {r['currency']}",
                r["next_pay_date"] or "-",
                ("-" if r["next_amount_base"] is None
                 else fmt_money(r["next_amount_base"])),
                ("-" if r["window_amount_base"] is None
                 else fmt_money(r["window_amount_base"]))])
        d.table(["TICKER", "ACCOUNT", "FREQ", "SHARES", "LAST/SH",
                 "NEXT", "NEXT AMT", f"{n}M"], body,
                aligns=["<", "<", "<", ">", ">", "<", ">", ">"],
                drop=(3, 4, 6))
    else:
        d.para("No position held now has a dividend history in the "
               "books.")
    t = doc["totals"]

    def _pair(x):
        return (f"taxable {fmt_money(x['taxable'])}, sheltered "
                f"{fmt_money(x['sheltered'])}")
    if rows:
        d.blank()
        yr = doc["tax_year"]
        roy = (_pair(t["rest_of_year"])
               if doc["as_of_date"] <= f"{yr}-12-31"
               else "the tax year is over")
        kv = [(f"Rest of tax year {yr}", roy),
              (f"Next {n} months", _pair(t["window"]))]
        if any(r["withholding_rate"] is not None for r in rows):
            kv.append((f"Withholding, {n} months",
                       _pair(t["withholding_window"])))
        d.kv(kv)
    # Every status but ok carries a flag; a flag on an ok row (a
    # currency left out, a row read without the books' say-so) is a
    # payer to check too.
    check = [r for r in rows if r["status"] != "ok" or r["flags"]]
    acts = []
    if check:
        syms = list(dict.fromkeys(r["symbol"] for r in check))
        acts.append(out.act(f"{len(syms)} payer(s) to check: "
                            f"{_few(syms, 2)}", f"{cmd} --details"))
    if doc["no_fx"]:
        acts.append(out.act(f"no {', '.join(doc['no_fx'])} rate in the "
                            f"books: those rows are out of the totals",
                            "tjs run"))
    if not details:
        if acts:
            d.blank()
            for a in acts:
                d.item(a[len(out.ACT):], bullet=out.ACT)
        om = doc["omitted_no_history"]
        if om:
            d.para(f"{len(om)} held position(s) with no dividend history "
                   f"in the books are not estimated.")
        d.para(out.details_hint(f"{cmd} --details",
                                "schedules, projected payments"))
        return d
    if acts:
        d.blank()
        for a in acts:
            d.item(a[len(out.ACT):], bullet=out.ACT)
    d.section("SCHEDULES AND PROJECTED PAYMENTS")
    for r in shown:
        proj = "; ".join(
            f"{p['pay_date']}"
            + (f" (tax date {p['tax_date']})"
               if p["tax_date"] != p["pay_date"] else "")
            + f" {fmt_money(p['amount'])} {r['currency']}"
            for p in r["projected"]) or "none"
        gap = (f", typical gap {r['median_gap_days']:g} days"
               if r["median_gap_days"] is not None and not _assumed(r)
               else "")
        wh = (f"; withholding {r['withholding_rate'] * 100:.1f}% (the "
              f"latest payment in {r['account']})"
              if r["withholding_rate"] is not None else "")
        fxs = (f"; at {r['fx_rate']:g} {base}/{r['currency']}"
               if r["fx_rate"] not in (None, 1.0) else "")
        fl = (" Check: " + "; ".join(r["flags"]) + "."
              if r["flags"] else "")
        d.item(f"{r['symbol']} ({r['account']}): {r['payments']} "
               f"payment(s)"
               + (f", {r['frequency']}" if r['frequency'] != "1 payment"
                  else "") + (" (assumed)" if _assumed(r) else "")
               + f"{gap}; last "
               f"{r['last_pay_date']} at {_ps(r['last_per_share'])} "
               f"{r['currency']}/share, 12-month {_ps(r['ttm_per_share'])}"
               f"/share; projected: {proj}{wh}{fxs}.{fl}", indent="  ")
    d.section("NOT ESTIMATED (no dividend history in the books)")
    if doc["omitted_no_history"]:
        d.para(", ".join(f"{o['symbol']} ({o['account']})"
                         for o in doc["omitted_no_history"]), indent="  ")
    else:
        d.line("  (none)")
    d.section("HOW IT IS ESTIMATED")
    # The project's own dating rule: a Canadian trust's record date is
    # Canada's (CA-INC-DATE-TRUST); a US project dates by the pay date,
    # a listed RIC's January dividend the year before (US-PLAN-07;
    # backlog TJ-0140: the Canadian rule was shown in a US project).
    from taxjson.lib.country import canonical_country, is_usa
    try:
        _us = is_usa(canonical_country(country)) if country else None
    except ValueError:
        _us = None
    dating = {True: " (a fund in ric_january_dividends: its January "
                    "dividend in the year before)",
              False: " (a Canadian trust's: its record date)"}.get(_us, "")
    d.items(_how(dating), "  ")
    return d
