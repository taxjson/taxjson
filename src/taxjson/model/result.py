"""The gains output as a typed Result, read from its JSON and back (step 6c).

A gains file (`<account>_gains.json`, the raw books' `_raw_gains.json`)
holds what lib/pipeline `run_gains` returns: one JSON object whose
`transactions` are the year's gain and income rows,
`manual_reporting_required` the rows routed out to be reported by hand (a
missing-history basis), `wash_sales` the wash records, `inventory` the
positions held, `lots_at` a US run's lots on a day (taxjson-gains
`--lots-at`), `summary` and `by_ticker` the totals, and other parts beside
them (`metadata`, `inventory_year_end`, `option_replacement_warnings`, ...),
read with `get`. (A `_gains_wash.json` is the wash stage's: the same parts,
money not rounded.)

`Result.from_json(doc)` copies the document down to its rows, read-only
(its parts in its order: a list as a tuple of read-only rows, an object as
a read-only mapping; the rows' nested objects are the document's own), and
reads a part when it is asked for: a part the reader never asks for is never
read, nor refused. `gains_json(doc)` is the document as run_gains writes it:
money rounded half-up to 4 places by key, quantities and day counts left as
they are (model/money `round_floats`), the cost timeline not rounded and
last (a null one dropped, as run_gains dropped it). `to_json()` is
gains_json of the Result's document, its lists lists again, so a recorded
`*_gains.json` comes back byte for byte.

A row (`GainRow`, `Holding`, `Lot`, `WashRecord`) is its JSON object, read-only and
in its order (`fields`), with typed values read from it as the readers read
them: `quantity` is the JSON `qty`; money is a Decimal of the number (a
float's repr, so float() of it is the float the JSON held); None when the
key is absent or null (or holds an empty string, list or object: a reader's
`or 0` read those as 0). A text field or a day count is the value as held;
the rest of a row is `get(key)`. A wash record's figures sit at its top or
under `loss_tx`, by the engine that wrote it, so it has no typed values
yet. A part that is null or empty of any type reads as empty (the readers'
`or []`); one of the wrong shape (a `transactions` that is not a list of
objects, a `summary` that is not an object) is refused with
ResultShapeError when it is read, and so is a typed value that is no number
(its key named). A row's nested objects (a `grant_closed`,
a `loss_tx`) are the document's own, not copies. None of the classes
hashes: their fields are mappings."""
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Callable, Dict, Generic, Mapping, Optional, Tuple, TypeVar, overload

from taxjson.model.money import float_decimal, round_floats, text_decimal

T = TypeVar("T")
P = TypeVar("P")

_EMPTY: Mapping[str, Any] = MappingProxyType({})

# The cost timeline's rows are lists: round_floats would round their
# quantities as money (a coin's dust to 0). It is written as it is.
COST_TIMELINE = "cost_timeline"


class ResultShapeError(ValueError):
    """A gains document, or a part of one, of the wrong shape."""


def number(value: Any) -> Optional[Decimal]:
    """A row's number as a Decimal: an int's value, a float's repr (a NaN
    or an infinity too), a Decimal as it is, a string's digits
    (model/money `text_decimal`: ValueError for one float() would not
    read); None for None and for an empty string, list or object. float()
    of it is float() of the value."""
    if isinstance(value, float):
        return float_decimal(value)
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(int(value))
    if isinstance(value, str) and value:
        return text_decimal(value)
    if not value:
        return None
    raise TypeError(f"not a number: {value!r}")


class _Field(Generic[T]):
    """A row's typed value: `read` of the row's `key` (the value as held
    when `read` is None)."""

    __slots__ = ("key", "read")

    def __init__(self, key: str, read: Optional[Callable[[Any], T]]) -> None:
        self.key = key
        self.read = read

    @overload
    def __get__(self, row: None, owner: type) -> "_Field[T]": ...

    @overload
    def __get__(self, row: "Row", owner: type) -> T: ...

    def __get__(self, row: Optional["Row"], owner: Optional[type] = None) -> Any:
        if row is None:
            return self
        value = row.fields.get(self.key)
        if self.read is None:
            return value
        try:
            return self.read(value)
        except (TypeError, ValueError):
            raise ResultShapeError(f'"{self.key}" is {value!r}, not a number') from None


def _money(key: str) -> "_Field[Optional[Decimal]]":
    return _Field(key, number)


def _text(key: str) -> "_Field[Optional[str]]":
    return _Field(key, None)


def _held(key: str) -> "_Field[Any]":
    """A value as the row holds it (no annotation in a row class: that
    would make it a dataclass field)."""
    return _Field(key, None)


def _flag(key: str) -> "_Field[bool]":
    return _Field(key, bool)


@dataclass(frozen=True, slots=True)
class Row:
    """One object of a gains document's list, as held (`fields`, in its
    order): `get` reads a key, `in` asks whether it is there. A Result's
    rows hold read-only copies; a reader whose rows flow on as the
    document's own dicts reads one through a view instead (`GainRow(e)`,
    `Holding(item)`: the dict itself, not a copy; step 6c(3))."""
    fields: Mapping[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self.fields


@dataclass(frozen=True, slots=True)
class GainRow(Row):
    """A row of `transactions` or `manual_reporting_required`: a
    disposition (no `action`) or an income row (DIVIDEND,
    DIVIDEND_IN_LIEU, ...)."""
    quantity = _money("qty")
    symbol = _text("symbol")
    account = _text("account")
    currency = _text("currency")
    date = _text("date")
    date_settle = _text("date_settle")
    action = _text("action")
    id = _text("id")
    time = _text("time")
    term = _text("term")
    direction = _text("direction")
    note = _text("note")
    acquired_date = _text("acquired_date")
    days_held = _held("days_held")
    proceeds = _money("proceeds")
    cost = _money("cost")
    gain = _money("gain")
    raw_gain = _money("raw_gain")
    commission = _money("commission")
    fee = _money("fee")
    disallowed_amount = _money("disallowed_amount")
    permanently_disallowed = _money("permanently_disallowed")
    deferred_loss_recovered = _money("deferred_loss_recovered")
    capitalized_263h = _money("capitalized_263h")
    denied_contribution = _money("denied_contribution")
    dividend = _money("dividend")
    pil = _money("pil")
    is_option = _flag("is_option")
    is_wash_sale = _flag("is_wash_sale")
    grant = _flag("grant")
    deemed = _flag("deemed")
    tainted = _flag("tainted")
    paid_on_short = _flag("paid_on_short")


def gain_before_denial(row: GainRow) -> Optional[Decimal]:
    """A row's gain before any denial: its `raw_gain`, else its `gain`
    (the engines write raw_gain on every disposition; a hand-run row
    without it is read as the readers always read it, not derived from
    a denial it shows); a null raw_gain reads None, not the gain
    (backlog TJ-2922, TJ-3025, TJ-3033). The one rule: the traces,
    edge_cases, wash_radar and plan read it here."""
    return row.raw_gain if "raw_gain" in row else row.gain


def money_keys(view: type) -> Tuple[str, ...]:
    """The JSON keys of a view's money fields (a GainRow's `qty`,
    `proceeds`, ...): what a view of that kind can refuse as not a
    number, the fields a reader holds to their types first."""
    return tuple(f.key for f in vars(view).values()
                 if isinstance(f, _Field) and f.read is number)


@dataclass(frozen=True, slots=True)
class Holding(Row):
    """A row of `inventory` (or `inventory_year_end`): a position held at
    the end of the run (of the project year); `deferred_wash` the denied
    loss carried in its cost; `recognised_premium` a written option's
    premium already taxed at the write (grant timing)."""
    quantity = _money("qty")
    symbol = _text("symbol")
    account = _text("account")
    currency = _text("currency")
    total_cost = _money("total_cost")
    deferred_wash = _money("deferred_wash")
    recognised_premium = _money("recognised_premium")


@dataclass(frozen=True, slots=True)
class Lot(Row):
    """A row of `lots_at`: one US lot held on the day, in FIFO order
    within its account (engine/us/prepare `_us_lot_rows`): a short's
    units and basis (its proceeds) negative."""
    quantity = _money("qty")
    symbol = _text("symbol")
    account = _text("account")
    date = _text("date")
    holding_start = _text("holding_start")
    basis = _money("basis")
    deferred = _money("deferred")


@dataclass(frozen=True, slots=True)
class WashRecord(Row):
    """A row of `wash_sales`: a loss denied or deferred, and its
    replacements (read with `get`)."""


def _rows(key: str, value: Any, kind: Callable[[Mapping[str, Any]], Any]) -> Tuple[Any, ...]:
    if not value:
        return ()
    if not isinstance(value, tuple):
        raise ResultShapeError(f'"{key}" is a list of objects, not {_kind(value)}')
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ResultShapeError(f'"{key}" row {i} is an object, not {type(item).__name__}')
        out.append(kind(item))
    return tuple(out)


def _kind(value: Any) -> str:
    """A part's type as its JSON names it (a frozen list is a tuple, a
    frozen object a mappingproxy)."""
    if isinstance(value, Mapping):
        return "dict"
    if isinstance(value, tuple):
        return "list"
    return type(value).__name__


def _object(key: str, value: Any) -> Mapping[str, Any]:
    if not value:
        return _EMPTY
    if not isinstance(value, Mapping):
        raise ResultShapeError(f'"{key}" is an object, not {_kind(value)}')
    return value


def _frozen(value: Any) -> Any:
    """A part of the document, copied down to its rows, read-only: a
    list (or a tuple) as a tuple of its items, each object a read-only
    copy; an object a read-only copy."""
    if isinstance(value, (list, tuple)):
        return tuple(MappingProxyType(dict(item)) if isinstance(item, Mapping) else item
                     for item in value)
    if isinstance(value, Mapping):
        return MappingProxyType(dict(value))
    return value


def _plain(value: Any) -> Any:
    """A frozen part as JSON holds it: a tuple a list, each object a dict."""
    if isinstance(value, tuple):
        return [dict(item) if isinstance(item, Mapping) else item for item in value]
    if isinstance(value, Mapping):
        return dict(value)
    return value


def gains_json(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """A gains document as run_gains writes it: round_floats of every
    part, the cost timeline (when it is not null) as it is, last. The
    document is not changed."""
    doc = dict(doc)
    timeline = doc.pop(COST_TIMELINE, None)
    out: Dict[str, Any] = round_floats(doc)
    if timeline is not None:
        out[COST_TIMELINE] = timeline
    return out


@dataclass(frozen=True, slots=True)
class Result:
    """A gains document (`doc`: its parts as held, in its order), its
    typed parts read when they are asked for. Build one with from_json."""
    doc: Mapping[str, Any]
    _read: Dict[str, Any] = field(default_factory=dict, init=False, repr=False, compare=False)

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> "Result":
        """The Result of a gains document: a JSON object, or run_gains's
        dict before it is written."""
        if not isinstance(doc, Mapping):
            raise ResultShapeError(f"a gains document is an object, not {type(doc).__name__}")
        return cls(MappingProxyType({key: _frozen(value) for key, value in doc.items()}))

    def _part(self, key: str, read: Callable[[str, Any], P]) -> P:
        if key not in self._read:
            self._read[key] = read(key, self.doc.get(key))
        part: P = self._read[key]
        return part

    @property
    def transactions(self) -> Tuple[GainRow, ...]:
        return self._part("transactions", lambda k, v: _rows(k, v, GainRow))

    @property
    def manual_reporting_required(self) -> Tuple[GainRow, ...]:
        return self._part("manual_reporting_required", lambda k, v: _rows(k, v, GainRow))

    @property
    def wash_sales(self) -> Tuple[WashRecord, ...]:
        return self._part("wash_sales", lambda k, v: _rows(k, v, WashRecord))

    @property
    def inventory(self) -> Tuple[Holding, ...]:
        return self._part("inventory", lambda k, v: _rows(k, v, Holding))

    @property
    def lots_at(self) -> Tuple[Lot, ...]:
        return self._part("lots_at", lambda k, v: _rows(k, v, Lot))

    @property
    def summary(self) -> Mapping[str, Any]:
        return self._part("summary", _object)

    @property
    def by_ticker(self) -> Mapping[str, Any]:
        return self._part("by_ticker", _object)

    def get(self, key: str, default: Any = None) -> Any:
        """A part of the document, read-only (`metadata`,
        `inventory_year_end`, ...: a list as a tuple)."""
        return self.doc.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self.doc

    def to_json(self) -> Dict[str, Any]:
        """The document as run_gains writes it (gains_json), its parts
        as JSON holds them (a list a list again)."""
        return gains_json({key: _plain(value) for key, value in self.doc.items()})


# frozen=True would give each class a hash of its fields, which are
# mappings: it fails on every one. None of them hashes.
for _cls in (Row, GainRow, Holding, Lot, WashRecord, Result):
    setattr(_cls, "__hash__", None)
del _cls
