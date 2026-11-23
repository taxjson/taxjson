"""taxjson.toml's [accounts.<name>] tables and its [estimate] table, typed
(the refactoring toward the 2026-11-02 architecture review, step 2c).

Unlike `Settings`, these are views beside the tables, not in their place:
the account tables are written (`tjs init`, `migrate`), dumped into the
close-year lock and handed to child tools as they are, so `cfg["accounts"]`
and `cfg["estimate"]` stay the dicts the file gave, and a reader asks
`accounts_of(cfg)` or `estimate_of(cfg)` for the typed ones. The fields are
the one source of the key lists (`ACCOUNT_KEYS`, `ESTIMATE_KEYS`, which
`lib/config_check` re-exports for the config check and the template); no
coercion: the values as the checked file gives them.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from taxjson.project.settings import TypedTable


@dataclass(frozen=True, eq=False)
class AccountSettings(TypedTable):
    """One [accounts.<name>] table. `taxable` and `sheltered` read the type
    the config check made exact (lib/config_check.ACCOUNT_TYPES)."""
    name: str
    type: Optional[str] = None                  # "taxable" | "sheltered"
    crypto: Optional[bool] = None
    transfers: Optional[bool] = None
    plan: Optional[str] = None                  # a sheltered plan's kind
    brokerage: Optional[str] = None             # the fetch plugin's source
    account: Optional[str] = None               # ...its account id
    query_id: Optional[str] = None              # ...its query id
    holdings: Optional[List[str]] = None        # the broker's holdings reports
    broker_accounts: Optional[List[str]] = None
    combined_broker_accounts: Optional[bool] = None
    exercise_fee: Optional[float] = None
    year_end_posting: Optional[str] = None      # "MM-DD"
    custodian_country: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    present: Tuple[str, ...] = ()
    _keys: FrozenSet[str] = field(init=False, repr=False, default=frozenset())
    _OWN_EXTRA = ("name",)

    @property
    def taxable(self) -> bool:
        return self.type == "taxable"

    @property
    def sheltered(self) -> bool:
        return self.type == "sheltered"

    @property
    def holdings_paths(self) -> Optional[List[Any]]:
        """What `holdings` names, as a list: one path (a string) is a
        one-item list, unset or empty []; None when it is neither a string
        nor a list (`tjs sanity` says it is not checked). The one reading
        of the setting: pipeline/sanity, lib/holdings_dir `listed_files`,
        lib/ticker_map_suggest and lib/clean (whose refusal of a holdings
        file in work/ or reports/ must see what sanity reads) ask it."""
        raw = self.holdings
        if not raw:
            return []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, (list, tuple)):
            return list(raw)
        return None


@dataclass(frozen=True, eq=False)
class EstimateSettings(TypedTable):
    """The [estimate] table: what the estimate takes from the user."""
    other_income: Optional[float] = None
    other_losses: Optional[float] = None
    deductions: Optional[float] = None
    carrying_charges: Optional[float] = None
    long_term_losses: Optional[float] = None
    amt_carryover: Optional[Any] = None         # {year: amount} (Canada)
    extra: Mapping[str, Any] = field(default_factory=dict)
    present: Tuple[str, ...] = ()
    _keys: FrozenSet[str] = field(init=False, repr=False, default=frozenset())


def accounts_of(cfg: Optional[Mapping[str, Any]]) -> Dict[str, AccountSettings]:
    """{name: its typed table}, in the file's order; an entry that is no table
    (which the config check refuses) is an empty one, so a lenient read still
    names the account."""
    return typed_accounts((cfg or {}).get("accounts"))


def typed_accounts(tables: Any) -> Dict[str, AccountSettings]:
    """The [accounts] table typed, whichever shape a caller holds: the run's
    view (every value typed) is returned as is, a table of the file's dicts
    is wrapped into a new dict; anything that is no table of tables is
    empty. Helpers with callers of both kinds (the run's typed view, a
    query's raw table) take theirs through it."""
    out: Dict[str, AccountSettings] = {}
    if not isinstance(tables, Mapping):          # `accounts = 5`, [[accounts.x]]
        return out
    if isinstance(tables, dict) and all(
            isinstance(t, AccountSettings) for t in tables.values()):
        return tables
    for name, table in tables.items():
        out[str(name)] = typed_account(table, str(name))
    return out


def _transfers_accounts(cfg: Dict[str, Any]) -> List[str]:
    """Accounts with `transfers = true`: their TRANSFER rows are in the
    books, so they keep no transfer sidecar."""
    return [n for n, a in accounts_of(cfg).items()
            if a.transfers]


def configured_accounts(tables: Any) -> Dict[str, AccountSettings]:
    """The entries of the [accounts] table that are tables, typed: a
    leniently read `margin = 42` is left out, where `accounts_of` /
    `typed_accounts` name it as an empty table. For the readers that
    must not treat such an entry as an account (its files are checked
    loose, it is not "unchecked", its lines are not "not taxable"). A
    typed view passes through; anything that is no table of tables is
    empty."""
    if not isinstance(tables, Mapping):
        return {}
    if isinstance(tables, dict) and all(
            isinstance(a, AccountSettings) for a in tables.values()):
        return tables
    return typed_accounts({str(n): a for n, a in tables.items()
                           if isinstance(a, Mapping)})


def sheltered_by_name(accounts: Mapping[str, AccountSettings]) -> Dict[str, bool]:
    """{name: is sheltered} of the accounts with a type (taxable or
    sheltered); one with no type, or another, is left out."""
    return {n: a.sheltered for n, a in accounts.items()
            if a.type in ("taxable", "sheltered")}


def typed_account(table: Any, name: Optional[str] = None) -> AccountSettings:
    """One account's table typed: a view passes through (renamed when the
    caller's `name` differs), a dict is wrapped, anything else is an empty
    table; `name` None keeps a view's own and leaves a dict's empty."""
    if isinstance(table, AccountSettings):
        return table if name is None or table.name == name \
            else dataclasses.replace(table, name=name)
    return AccountSettings.from_table(
        table if isinstance(table, Mapping) else {}, name=name or "")


def account_of(accounts: Mapping[str, AccountSettings], name: str) -> AccountSettings:
    """`accounts_of(cfg)[name]`, or an empty table (no key set) for a name
    the file doesn't configure: the typed form of `(accounts.get(name) or
    {}).get(key)`."""
    return accounts.get(name) or AccountSettings.from_table({}, name=name)


def estimate_of(cfg: Optional[Mapping[str, Any]]) -> EstimateSettings:
    """The [estimate] table, typed; empty for a cfg without one."""
    return typed_estimate((cfg or {}).get("estimate"))


def typed_estimate(table: Any) -> EstimateSettings:
    """The [estimate] table typed, whichever shape a caller holds: a view
    passes through, a dict is wrapped, anything else is an empty table."""
    if isinstance(table, EstimateSettings):
        return table
    return EstimateSettings.from_table(table if isinstance(table, Mapping) else {})


ACCOUNT_KEYS: Tuple[str, ...] = AccountSettings.keys_known()
ESTIMATE_KEYS: Tuple[str, ...] = EstimateSettings.keys_known()
