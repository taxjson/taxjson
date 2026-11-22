"""The engine for a country: the one module importing both engines.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.rules.country import (
    CountryError as _CountryError, canonical_country as _canonical_country,
)

from taxjson.engine.taxrules import TaxRules
from taxjson.engine.us.taxrules import USATaxRules

from taxjson.engine.ca.taxrules import CanadaTaxRules


def get_tax_rules(country: str) -> TaxRules:
    """The engine for a country (rules/country.canonical_country: an
    unknown value raises CountryError, a ValueError)."""
    c = _canonical_country(country)
    for engine in (CanadaTaxRules, USATaxRules):
        if engine.country == c:
            return engine()
    raise _CountryError(f"no engine for {c!r}")      # unreachable: both named
