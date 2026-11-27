"""taxjson.toml's [settings], typed and read once.

`Settings` has a field for every key `lib/country.SETTING_COUNTRY` names (the
one table of the keys and the country that owns each), and holds what the file
gives once `pipeline/config` has checked and canonicalised it: nothing here
coerces a value. `load_config` builds it, with or without `missing_ok`; a
command reads it from `cfg["settings"]` as before.

The object is its attributes: a key the file leaves out is None, `has(key)`
tells that from a value, `empty` is a table with no key, `present` the keys in
the file's order, `extra` the ones taxjson does not read (kept for the
unknown-key warning), `as_dict` the table as the file set it. It was a
read-only mapping too while the dict-era readers turned into attribute reads,
one file at a time; the mapping went with the last of them (TJ-1635).
`as_settings` builds the object from a raw table (a lock's JSON, a parsed
document, a test's dict) at the entry points that take either.
Step 2 of the refactoring toward the 2026-11-02 architecture review (F5).
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field, fields, replace as _replace
from types import MappingProxyType
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple


class TypedTable:
    """A frozen dataclass over one taxjson.toml table (the [settings] table,
    an [accounts.<name>] table, [estimate]): a field per known key, `extra`
    for the keys this taxjson does not read, `present` for the keys the file
    sets, in its order. No coercion: the values as the table gives them once
    the config checks have canonicalised it. It is its attributes: `has(key)`
    tells an unset key from a value, `empty` a table with no key, `as_dict`
    the table as the file set it; it is no mapping (a dict is never equal to
    it: compare `as_dict()`). A subclass is a frozen dataclass
    with `eq=False` (`==` on the items, in any order; no hash) that declares
    the three fields below after its keys."""
    extra: Mapping[str, Any]
    present: Tuple[str, ...]
    _keys: FrozenSet[str]
    # The fields every table has beside its keys; a subclass names its own
    # in `_OWN_EXTRA` (an account's `name`).
    _OWN: Tuple[str, ...] = ("extra", "present", "_keys")
    _OWN_EXTRA: Tuple[str, ...] = ()

    @classmethod
    def keys_known(cls) -> Tuple[str, ...]:
        """The table's keys this taxjson reads, in the class's order."""
        return _keys_known(cls)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_keys", frozenset(self.present))
        if not isinstance(self.extra, MappingProxyType):
            object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    @classmethod
    def from_table(cls, table: Mapping[str, Any], **own: Any):
        """The table as checked and canonicalised (`own`: the object's own
        fields beside the keys, such as an account's name)."""
        if isinstance(table, TypedTable):
            table = table.as_dict()
        known_keys = cls.keys_known()
        # One rule with `replace`: a key set to None is absent (a lock's
        # JSON can hold null; TOML cannot), so `has` means "set to a value".
        table = {k: v for k, v in table.items() if v is not None}
        known = {k: v for k, v in table.items() if k in known_keys}
        extra = {k: v for k, v in table.items() if k not in known_keys}
        return cls(**own, **known, extra=extra, present=tuple(table))

    # ---- equality: the same kind of table, the same own fields, the same
    # keys and values (the order of the keys does not count); no hash.

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented           # a dict is never equal: compare as_dict()
        return self._own() == other._own() and self.as_dict() == other.as_dict()

    __hash__ = None             # type: ignore[assignment]

    # ---- the typed spellings of "is the key set" and "is the table empty"

    def has(self, key: str) -> bool:
        """Whether the file sets `key` to a value (one set to None is
        absent, as `replace` reads it): the attribute alone cannot tell
        an unset key from a value of None."""
        return key in self._keys

    @property
    def empty(self) -> bool:
        """No key set: the table of a project without the file, or
        without the section."""
        return not self.present


    def __repr__(self) -> str:
        own = "".join(f"{k}={v!r}, " for k, v in self._own().items())
        return f"{type(self).__name__}({own}{self.as_dict()!r})"

    # ---- the frozen object

    def replace(self, **changes: Any):
        """A copy with `changes` set (a key set this way is present; a None
        change takes the key out: absent, not None, TJ-1602)."""
        known_keys = self.keys_known()
        unknown = [k for k in changes if k not in known_keys]
        if unknown:
            raise TypeError(f"not a key of the table: {', '.join(unknown)}")
        present = (tuple(k for k in self.present if changes.get(k, 0) is not None)
                   + tuple(k for k in changes
                           if k not in self.present and changes[k] is not None))
        return _replace(self, present=present, **changes)

    def as_dict(self) -> Dict[str, Any]:
        """The table as the file set it, as a dict (the keys the file sets,
        in its order: the known ones from the fields, the rest from extra)."""
        return {k: self.extra[k] if k in self.extra else getattr(self, k)
                for k in self.present}

    def _own(self) -> Dict[str, Any]:
        """The object's own fields beside the three every table has (an
        account's `name`)."""
        return {k: getattr(self, k) for k in self._OWN_EXTRA}

    def __reduce__(self):
        # deepcopy and pickle rebuild it from its items (a mappingproxy can't
        # be pickled; TJ-1601).
        return (_rebuild, (type(self), self.as_dict(), self._own()))


def _rebuild(cls, table: Dict[str, Any], own: Dict[str, Any]):
    return cls.from_table(table, **own)


@functools.lru_cache(maxsize=None)
def _keys_known(cls) -> Tuple[str, ...]:
    skip = set(cls._OWN) | set(cls._OWN_EXTRA)
    return tuple(f.name for f in fields(cls) if f.name not in skip)


# The fields follow lib/country.SETTING_COUNTRY, the one table of the keys.
# `eq=False`: TypedTable's `==` (the items, in any order; never a dict) and its
# `__hash__ = None` (TJ-1593).
@dataclass(frozen=True, eq=False)
class Settings(TypedTable):
    # Every key, in SETTING_COUNTRY's order (tests/features/configuration/test_typed_settings_object.py
    # holds the two together).
    year: Optional[int] = None
    country: Optional[str] = None
    base_currency: Optional[str] = None
    tax_date: Optional[str] = None
    source_currencies: Optional[Any] = None
    cross_asset: Any = None                     # retired; warned and ignored
    fx_cash_gains: Optional[bool] = None
    fx_cash_ledger: Optional[str] = None
    fx_cash_inflow_cost: Optional[str] = None
    futures_settle: Optional[str] = None
    prior_year_record: Optional[str] = None
    inputs_dir: Optional[str] = None
    holdings_dir: Optional[str] = None
    exports_dir: Optional[str] = None
    local_timezone: Optional[str] = None
    leaps_months: Optional[int] = None
    requires_taxjson: Optional[str] = None
    transfers_as_acquisitions: Optional[bool] = None
    sheltered_elections: Optional[str] = None        # "zero" | "ask"
    benchmark: Optional[str] = None                  # a symbol (technicals)
    log_keep: Optional[int] = None                   # logs/ (lib/command_log)
    province: Optional[str] = None
    option_premium_timing: Optional[str] = None
    option_grant_timing_since: Optional[int] = None
    option_buyback_loss_superficial: Optional[bool] = None
    foreign_return_of_capital: Optional[str] = None
    corporate_distributions: Optional[List[str]] = None  # symbols
    tobase_map: Optional[str] = None
    ric_january_dividends: Optional[List[str]] = None  # "SYM [DATE]" entries
    # The keys this taxjson does not read, as the file gives them.
    extra: Mapping[str, Any] = field(default_factory=dict)
    # The keys the file sets, in its order.
    present: Tuple[str, ...] = ()
    _keys: FrozenSet[str] = field(init=False, repr=False, default=frozenset())


def as_settings(table: Optional[Mapping[str, Any]]) -> "Settings":
    """`table` as the typed object: itself when it is one, else built from the
    mapping (a test's dict, a lock's [settings] table, a bin tool's raw read:
    its values as they come, unchecked), or empty for None (a cfg with no
    [settings]).
    The helpers under lib/ take their settings through this until every
    caller hands them the object (PLAN step 2b's end)."""
    if isinstance(table, Settings):
        return table
    # A `settings` that is no table (`settings = 5`) is empty here: the config
    # check names it ("[settings] must be a table"); no reader tracebacks on it.
    return Settings.from_table(table if isinstance(table, Mapping) else {})


def settings_of(cfg: Optional[Mapping[str, Any]]) -> "Settings":
    """A config's [settings] as the typed object (`as_settings` of its table;
    empty for a cfg without one, or None): the one spelling for the readers
    that are handed a whole config (backlog TJ-1632)."""
    return as_settings((cfg or {}).get("settings"))


# The known keys: the fields but the two the object keeps for itself.
# (tests/features/configuration/test_typed_settings_object.py holds them to SETTING_COUNTRY's order.)
KEYS: Tuple[str, ...] = Settings.keys_known()
