"""What a parse is told about the project (step 5h of the engine
refactor): one frozen object the brokerage tool builds from its
arguments (`taxjson_brokerage._parse_settings`) and hands to the parser
(`BaseBrokerage.configure`), in place of setting
attributes on the instance one by one behind `hasattr` probes. Every
field is an input: the country chooses the law a message cites and
nothing else here; what the country decides of the parse (the
stablecoin model, the plans taxed as ordinary) is rules/country
`parse_basis`, which `ParseSettings.for_country` passes (refused beside
a country when left out, so a country's parse never takes the neutral
basis by omission); what follows from the parser itself (IB's deferred messages)
the parser decides in its own `configure`. A parser reads the attributes it needs and ignores the
rest; one run bare, as the tests do, keeps its class defaults."""
from dataclasses import dataclass, fields
from typing import FrozenSet, Optional, Tuple


@dataclass(frozen=True)
class ParseSettings:
    # The project's country (rules/country): the law a message cites.
    country: Optional[str] = None
    # The EXTRACT lines (taxjson-brokerage --security-overrides).
    security_overrides: Tuple[tuple, ...] = ()
    # Taxable or sheltered (None: not said); a sheltered account's rows
    # are booked without the taxable checks.
    account_taxable: Optional[bool] = None
    # Two broker accounts' rows combined into one (audit S027-05).
    combined_broker_accounts: bool = False
    # The transfer costs are checked by a later stage (--transfers-out
    # without --transfers).
    transfer_costs_checked_downstream: bool = False
    # IB and the generic importer: futures settle on the trade date or
    # the next day.
    futures_settle: str = "trade"
    # Webull: the fee of an exercise leg ([accounts.<name>] exercise_fee).
    exercise_fee: Optional[float] = None
    # The basis (rules/country `parse_basis`): USD stablecoins as
    # US-dollar cash (CA-CRYPTO-02) or property like any coin
    # (US-CRYPTO-02); the registered plans taxed like an ordinary account
    # (US-BASIS-09, backlog TJ-0613). None: not said, the neutral answers
    # (property, no plan), which only a parse without a country takes.
    stablecoins_as_cash: Optional[bool] = None
    plans_taxed: Optional[FrozenSet[str]] = None

    def __post_init__(self) -> None:
        from taxjson.rules.country import canonical_country
        # The country as every reader spells it (rules/country), or none:
        # a blank is none, and none takes the neutral answers (the reader
        # refuses to guess).
        country = (canonical_country(self.country)
                   if isinstance(self.country, str) and self.country.strip()
                   else None)
        object.__setattr__(self, "country", country)
        if country is not None and (self.stablecoins_as_cash is None
                                    or self.plans_taxed is None):
            raise TypeError(
                f"ParseSettings: a {country} parse is told its basis: build "
                f"it with ParseSettings.for_country (rules/country parse_basis)")
        object.__setattr__(self, "stablecoins_as_cash", bool(self.stablecoins_as_cash))
        object.__setattr__(self, "plans_taxed", frozenset(self.plans_taxed or ()))

    @classmethod
    def for_country(cls, country: Optional[str], **given) -> "ParseSettings":
        """The settings of a parse of `country` on that country's own
        basis (rules/country `parse_basis`; none: the neutral one): what
        the brokerage tool and the conformance check build, so a parse
        never takes another country's basis. The constructor takes any
        basis as given (the test of the parse's country-blindness builds
        one apart from its country)."""
        from taxjson.rules.country import parse_basis
        return cls(country=country, **parse_basis(country), **given)

    @classmethod
    def names(cls) -> Tuple[str, ...]:
        return tuple(f.name for f in fields(cls))

