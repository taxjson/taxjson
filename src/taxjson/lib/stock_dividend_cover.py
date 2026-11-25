"""A stock dividend posted after the short that owes it was covered: the
one decision that the row books no shares (tax-logic CA-STKDIV-02 /
US-STKDIV-06), made from the rows alone.

A short open on the ex-dividend date owes the dividend's shares to the
lender. Where the account bought the short back before the row was
posted, the row meets no short: it is that short's obligation, which its
cover did not include, and no shares received. Both gains engines call
`no_share_rows` on the book they walk, and their answer is the one
source of truth: they write the rows' ids into the gains files
(`stock_dividends_after_cover`). A reader that runs after the gains run
(the wash radar, `tjs t1135`'s unit walk, the holdings report) reads
that record (`booked_rows`) and never decides again: its own rows are
fewer than the engine's (no synthesized opening, a sheltered book's
transfer not yet held units). The `[[distributions]]` sizing runs
before the gains run: it decides on the book that run will walk
(`presized`), and the run checks the two against each other afterwards
(`presize_disagreements`: an ATTENTION naming both readings), so no
reader counts shares another leaves out in silence (backlog TJ-5282).

How an account's short and held units stand before a row is the
caller's reader (`positions`): a netted position by default, and for
Canada the walk's own marked-short router (engine/ca/router
`MarkedPositions`), which `no_share_rows_of` imports when it is called:
the engines import this module at their top, and the router's predicate
has one copy.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from taxjson.lib.row_types import (OPENING_TYPE, REORG_356_TYPE,
                                   SPINOFF_355_TYPE, TRANSFER_BOOK_VALUE_TYPE,
                                   TRANSFER_CUSTODY_TYPE, is_stock_dividend)

# A row with no entitlement date is named (never dropped) where the
# account bought a short back within this many days before it.
NEAR_COVER_DAYS = 90
_EPS = 1e-9
# Units that arrive with a date of their own (the US engine's lots keep
# it): an opening's lot date is on the row; the others' dates are not.
_CARRIED_TYPES = (OPENING_TYPE, TRANSFER_BOOK_VALUE_TYPE, TRANSFER_CUSTODY_TYPE,
                  SPINOFF_355_TYPE, REORG_356_TYPE)


def _f(row: Any, name: str, default: Any = '') -> Any:
    """A row's field, dict or TaxTransaction."""
    v = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
    return default if v is None else v


def _units(row: Any) -> float:
    try:
        return float(_f(row, 'quantity', 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def entitlement_date(row: Any) -> str:
    """A stock dividend row's entitlement date: its ex-dividend date,
    else its record date ('' with neither)."""
    return str(_f(row, 'ex_date') or _f(row, 'record_date') or '').strip()[:10]


def _days(a: str, b: str) -> Optional[int]:
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except ValueError:
        return None


def _renames(rows: Sequence[Any]) -> Dict[str, str]:
    """{old symbol: new symbol} of the books' ticker changes (a SPLIT row
    whose `symbol_new` is another symbol)."""
    out: Dict[str, str] = {}
    for r in rows:
        new = str(_f(r, 'symbol_new') or '')
        if _f(r, 'action') == 'SPLIT' and new and new != _f(r, 'symbol'):
            out[str(_f(r, 'symbol'))] = new
    return out


def _security(symbol: str, renames: Dict[str, str]) -> str:
    seen = set()
    while symbol in renames and symbol not in seen:
        seen.add(symbol)
        symbol = renames[symbol]
    return symbol


def _carried(row: Any) -> Optional[str]:
    """None for a trade; for units that arrive with a date of their own,
    that date, '' when the row does not say."""
    if _units(row) <= 0:
        return None
    action, typ = _f(row, 'action'), str(_f(row, 'type') or '')
    if action == 'BUYSELL' and typ == OPENING_TYPE:
        return str(_f(row, 'lot_date') or _f(row, 'date'))[:10]
    if action in ('TRANSFER', 'OPENING_BALANCE') or typ in _CARRIED_TYPES:
        return ''
    return None


class NetPositions:
    """An account's units of one security as one netted position: short
    below 0, held above. The US engine's reading (its lots net), and the
    default. Canada's engine keeps a broker-marked short in a pool of its
    own while the account is long (CA-DISP-04): its books are read
    through engine/ca/router `MarkedPositions`, the walk's own router, so
    the predicate has one copy."""

    def __init__(self) -> None:
        self._units = 0.0

    def before(self, row: Any) -> Tuple[float, float]:
        """(units short, units held) before `row`."""
        return max(-self._units, 0.0), max(self._units, 0.0)

    def walk(self, row: Any, owed: bool = False) -> None:
        """Book `row` (`owed`: a stock dividend a short still open makes
        good: the short grows)."""
        if _f(row, 'action') != 'SPLIT':
            self._units += -_units(row) if owed else _units(row)


# A row before a stock dividend, as the decision reads it: (trade date,
# units short before it, units held before it, whether it bought a short
# back, its id, its lot's own date (`_carried`), its settlement date).
_Seen = Tuple[str, float, float, bool, Any, Optional[str], str]


def no_share_rows(rows: Iterable[Any], *, entitled_lots: bool,
                  pooled: bool = False, positions: Callable[[], Any] = NetPositions,
                  registered: bool = False
                  ) -> Tuple[Dict[Any, Dict[str, Any]], Dict[Any, Dict[str, Any]]]:
    """(the stock dividend rows that book no shares, the undated ones a
    short covered in the days before may owe): row id -> the facts.

    `rows`: the taxable books (dicts or TaxTransaction), any order. Each
    account's own position decides (`pooled`: one position across the
    accounts, the US engine's pooled lots), walked by trade date over
    its trades, its opening rows and its transfers in and out (a move
    between the person's accounts or a plain one), a ticker change
    followed. `positions` makes the reader of an account's units short
    and held (`NetPositions`; Canada: the walk's marked-short router). A
    stock dividend row (`row_types.is_stock_dividend`, units above 0),
    in order:

    1. a short still open at the row: not this rule (the engines' own
       reading: the short grows, or, beside shares held, the row is
       shares received);
    2. no entitlement date on the row (`entitlement_date`): nothing
       tells. Named in the second dict where the account bought a short
       back within NEAR_COVER_DAYS before it;
    3. shares entitled to it: the account held shares on the entitlement
       date, or was not short on it (what the first row on or after the
       date found); or, with `entitled_lots` (the US, whose lots keep
       their dates), units arrived since with a date of their own: an
       opening whose lot date is before it, a lot moved in, a transfer
       booked at its book value, a spin-off's or an exchange's shares
       (dates the rows do not say);
    4. the account was short on the date and a purchase since bought the
       short back: the row is that short's, no shares. `cover`: the last
       such purchase, which a `delivered_cost` on the row is a cost of.

    An account that held shares on the date and was short beside them (a
    marked short, bought back since) keeps the row as its shares' (test
    3) and is named in the second dict with `beside` set: the short's
    own part is in no row.

    A row decided so adds no units to the position the later rows meet.

    `registered`: the book is a registered (sheltered) account's. Such an
    account is never short: a sale with no purchase before it is missing
    history, which the run says ("cannot be short"), so the rule never
    applies and both dicts are empty. The caller knows it as the gains
    run does: a main book that is not taxable (lib/pipeline
    `engine_options`' `registered_book`)."""
    rows = list(rows)
    if registered or not any(is_stock_dividend(r) and _units(r) > 0 for r in rows):
        return {}, {}
    renames = _renames(rows)
    books: Dict[Tuple[str, str], List[Tuple[Tuple[str, str, int], Any]]] = {}
    changes: Dict[str, List[Tuple[Tuple[str, str, int], Any]]] = {}
    for n, r in enumerate(rows):
        action = _f(r, 'action')
        at = (str(_f(r, 'date'))[:10], str(_f(r, 'time') or ''), n)
        if action == 'SPLIT':
            changes.setdefault(_security(str(_f(r, 'symbol')), renames), []).append((at, r))
            continue
        # (every row that adds or removes units, as the engines count
        # them: a transfer in or out too, whatever its type. A sheltered
        # book's plain transfer reaches the engine as held units.)
        if action not in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'TRANSFER'):
            continue
        if not _units(r):
            continue
        key = ('' if pooled else str(_f(r, 'account')),
               _security(str(_f(r, 'symbol')), renames))
        books.setdefault(key, []).append((at, r))
    gone: Dict[Any, Dict[str, Any]] = {}
    near: Dict[Any, Dict[str, Any]] = {}
    for key in sorted(books):
        if not any(is_stock_dividend(r) for _, r in books[key]):
            continue
        # (the security's splits and ticker changes, each once, whichever
        # account's rows carry them)
        events = {(at[0], str(_f(r, 'symbol')), str(_f(r, 'symbol_new') or '')): (at, r)
                  for at, r in changes.get(key[1], ())}
        book = sorted(books[key] + list(events.values()), key=lambda x: x[0])
        pos = positions()
        seen: List[_Seen] = []
        for (day, _time, _n), r in book:
            if _f(r, 'action') == 'SPLIT':
                pos.walk(r)
                continue
            short, held = pos.before(r)
            if not (is_stock_dividend(r) and _units(r) > 0):
                pos.walk(r)
                seen.append((day, short, held, pos.before(r)[0] < short - _EPS,
                             _f(r, 'id', None), _carried(r),
                             str(_f(r, 'date_settle') or day)[:10]))
                continue
            fact = _decide(r, seen, short, entitled_lots)
            if fact is not None and fact.pop('near', False):
                near[_f(r, 'id', None)] = fact
            elif fact is not None:
                gone[_f(r, 'id', None)] = fact
                continue                    # (no units)
            pos.walk(r, owed=short > _EPS and held <= _EPS)
            seen.append((day, short, held, False, _f(r, 'id', None), None, day))
    return gone, near


def _decide(r: Any, seen: Sequence[_Seen], short: float,
            entitled_lots: bool) -> Optional[Dict[str, Any]]:
    """`no_share_rows`' tests 1 to 4 for the stock dividend row `r`
    (`short`: the units short at it): its facts (with `near` set for an
    undated row beside a cover), or None."""
    if short > _EPS:
        return None
    ent, day = entitlement_date(r), str(_f(r, 'date'))[:10]
    facts: Dict[str, Any] = {
        'symbol': str(_f(r, 'symbol')), 'account': str(_f(r, 'account')),
        'date': day, 'time': str(_f(r, 'time') or '09:30:00'),
        # (the currency the row was written in: a base-currency book's row keeps
        # it as `trade_currency`. The example .tt line a message prints names
        # it, so a cost pasted in the broker's currency is read in it.)
        'currency': str(_f(r, 'trade_currency') or _f(r, 'currency') or 'CUR'),
        'units': _units(r),
        'cost': _f(r, 'delivered_cost', 0.0) or 0.0}
    if not ent:
        close = [s for s in seen if s[3]
                 and (_days(s[0], day) if _days(s[0], day) is not None else -1)
                 in range(NEAR_COVER_DAYS + 1)]
        if not close:
            return None
        return dict(facts, near=True, covered=sorted({s[0] for s in close}))
    since = [s for s in seen if s[0] >= ent]
    if since and since[0][2] > _EPS and since[0][1] > _EPS and any(s[3] for s in since):
        # Shares held and a marked short beside them on the date, the
        # short bought back since: the row is the shares' (test 3), and
        # the short's own part is in no row. Named, never dropped.
        return dict(facts, near=True, beside=True, entitlement=ent,
                    short=since[0][1], held=since[0][2],
                    covered=sorted({s[0] for s in since if s[3]}))
    if not since or since[0][2] > _EPS or not since[0][1] > _EPS:
        return None
    if entitled_lots and any(s[5] is not None and (not s[5] or s[5] < ent)
                             for s in since):
        return None
    covers = [s for s in since if s[3]]
    if not covers:
        return None
    return dict(facts, entitlement=ent, short=since[0][1],
                dated_by='ex' if str(_f(r, 'ex_date') or '').strip() else 'record',
                covered=sorted({s[0] for s in covers}), cover=covers[-1][4],
                cover_date=covers[-1][0], cover_settle=covers[-1][6])


def no_share_rows_of(rows: Iterable[Any], country: Optional[str], *,
                     pooled: bool = False, registered: bool = False
                     ) -> Tuple[Dict[Any, Dict[str, Any]], Dict[Any, Dict[str, Any]]]:
    """`no_share_rows` as a project of `country` reads its books: the US
    with its lots' own dates and one netted position (`pooled`: its lots
    pooled across accounts); Canada (and no country) with each account's
    marked short kept apart from its shares, by the walk's own router
    (engine/ca/router `MarkedPositions`: imported here, when called, so
    the predicate stays the engine's one copy)."""
    from taxjson.lib.country import is_usa
    if country and is_usa(country):
        return no_share_rows(rows, entitled_lots=True, pooled=pooled, registered=registered)
    from taxjson.engine.ca.router import MarkedPositions
    return no_share_rows(rows, entitled_lots=False, positions=MarkedPositions,
                         registered=registered)


def without_no_share_rows(rows: Sequence[Any], country: Optional[str]) -> List[Any]:
    """`rows` less the stock dividend rows that book no shares
    (`no_share_rows_of`, each account's own position): the book a walk
    that adds up units itself reads, in a project of `country`. For a
    reader that runs before the gains run only, on the book the engine
    will walk (`presized`); one that runs after it reads the engine's
    own answer (`booked_rows`)."""
    rows = list(rows)
    gone, _ = no_share_rows_of(rows, country)
    if not gone:
        return rows
    return [r for r in rows if _f(r, 'id', None) not in gone]


def presized(rows: Sequence[Dict[str, Any]], phantoms: Any, country: Optional[str],
             prog: str = 'taxjson', registered: bool = False
             ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(the book `[[distributions]]` sizes a record-date balance on, the
    ids of the stock dividend rows left out of it as no shares): an
    account's base book `rows` (dicts) as the gains run will walk it, so
    the decision is made on the engine's own book: the openings of its
    .tt `OPENING ... cost=unknown` lines synthesized first (`phantoms`,
    lib/missing_history `synthesize_openings`, the gains stage's own
    call), the ADJUST rows the tool itself wrote (`DIST-`) not in it.
    Sized on the book without the openings, a holder with a sale and a
    purchase around the ex date read as a covered short, and the row's
    shares went unsized (backlog TJ-5282)."""
    book = [t for t in rows if not str(t.get('id') or '').startswith('DIST-')]
    if phantoms:
        from taxjson.lib.tx_load import coerce_transaction_row
        from taxjson.lib.missing_history import synthesize_openings
        typed = [coerce_transaction_row(t, i, prog) for i, t in enumerate(book)]
        out, _log = synthesize_openings(typed, phantoms)
        book = book + [t.to_dict() for t in out[len(typed):]
                       if t.action == 'OPENING_BALANCE']
    # (`registered`: a registered account's book, where the rule never applies)
    gone, _ = no_share_rows_of(book, country, registered=registered)
    return ([r for r in book if _f(r, 'id', None) not in gone],
            sorted(str(i) for i in gone))


def booked_rows(rows: Iterable[Any], gains_files: Iterable[Any]) -> List[Any]:
    """`rows` less the stock dividend rows the gains run booked as no
    shares: the engines' own record in `gains_files` (their
    `stock_dividends_after_cover`), never a second reading of the rows.
    What a reader that runs after the gains run calls with the gains
    files it is given (the wash radar's --gains, the holdings report's,
    `tjs t1135`'s): a reader that decided it again from its own rows saw
    fewer of them than the engine walks (a synthesized opening, a
    sheltered book's transfer in) and dropped a dividend the engine
    booked (backlog TJ-5282). A file that does not read names no row:
    the reader's own load of it reports that."""
    import json
    from taxjson.lib.missing_history import STOCK_DIVIDENDS_AFTER_COVER
    gone: set = set()
    for path in sorted({str(p) for p in gains_files}):
        try:
            doc = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            gone.update(str(i) for i in doc.get(STOCK_DIVIDENDS_AFTER_COVER) or ())
    rows = list(rows)
    if not gone:
        return rows
    return [r for r in rows if str(_f(r, 'id', None)) not in gone]


def presize_disagreements(rows: Sequence[Dict[str, Any]], presized_ids: Iterable[str],
                          engine_ids: Iterable[str]) -> List[str]:
    """The lines for an account's stock dividend rows that
    `[[distributions]]` sized one way (`presized_ids`: left out as no
    shares, `presized`) and the gains run booked the other
    (`engine_ids`: its `stock_dividends_after_cover`), one per row of
    the base book `rows`; none when the two agree. The run says each
    after the gains run: a record-date balance sized on other units than
    the books hold is never silent."""
    pre, eng = {str(i) for i in presized_ids}, {str(i) for i in engine_ids}
    out = []
    for r in rows:
        rid = str(_f(r, 'id', None))
        if not is_stock_dividend(r) or (rid in pre) == (rid in eng):
            continue
        said = {True: 'no shares (a covered short\'s obligation)',
                False: 'shares received'}
        out.append(
            f"stock dividend: {_f(r, 'symbol')} ({_f(r, 'account')}): the row of "
            f"{_units(r):g} share(s) on {str(_f(r, 'date'))[:10]} was read as "
            f"{said[rid in pre]} when [[distributions]] sized its record-date "
            f"balances, and the gains run booked it as {said[rid in eng]}. The "
            f"books' reading stands; a [[distributions]] amount of "
            f"{_f(r, 'symbol')} with a record date after the row was sized on "
            f"{_units(r):g} share(s) too {'few' if rid in pre else 'many'}: "
            f"check its ADJUST row (`tjs list --details`), and report this "
            f"(`tjs --version`, this line).")
    return out
