"""The country resolver, a leaf.

Moved from lib/country.py (PLAN step 10), which re-exports every name;
it imports nothing from taxjson, so the parse and characterize may read
it (scripts/check_architecture.py's LEAVES).

- ``canonical_country(value)`` -> ``"canada"`` | ``"usa"``. Accepts only
  the spellings in ``ALIASES`` (any case, surrounding blanks ignored).
  Anything else, and a missing value, raises ``CountryError`` (a
  ``ValueError``) with one message.
- ``foreign_roc_choice(country, explicit)``: how a foreign issuer's
  return of capital is booked (characterize reads it; lib/country.py's
  ``foreign_roc_mode`` is it over a project's settings).

Test hook: ``check_engine_allowed(country)`` is called by both gains
engines. When the environment variable ``TAXJSON_TEST_ENGINE_COUNTRY``
is set (only the test markers in ``tests/tax_rules`` set it), running
the other country's engine raises ``AssertionError`` — a test tagged for
a Canadian rule cannot silently exercise the US engine, in-process or
in a ``taxjson`` subprocess.
"""
from __future__ import annotations
import os
from typing import Any, Dict, FrozenSet, Optional, Tuple


CANADA = "canada"
USA = "usa"
COUNTRIES = (CANADA, USA)

# The only accepted spellings (compared after strip().lower()).
ALIASES: Dict[str, str] = {"canada": CANADA, "ca": CANADA,
                           "usa": USA, "us": USA}

DISPLAY_NAME = {CANADA: "Canada", USA: "United States"}

ENGINE_GUARD_ENV = "TAXJSON_TEST_ENGINE_COUNTRY"


class CountryError(ValueError):
    """A missing, unknown or other-country value. str(e) is the whole
    user-facing message."""


_SPELLINGS = "canada, ca, usa or us"


def canonical_country(value: Any, *, what: str = "country") -> str:
    """"canada" | "usa" for an accepted spelling; CountryError for
    anything else, including None and ""."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise CountryError(
            f"{what} is missing — set it to \"canada\" or \"usa\" "
            f"(taxjson never guesses the country)")
    if not isinstance(value, str):
        raise CountryError(f"{what} must be {_SPELLINGS}, got {value!r}")
    canon = ALIASES.get(value.strip().lower())
    if canon is None:
        raise CountryError(f"{what} must be {_SPELLINGS}, got {value!r}")
    return canon


# Canadian registered plans that a country's return taxes like an
# ordinary account (tax-logic US-BASIS-09): the Canada-US treaty (Art.
# XVIII(7)) defers US tax only on a retirement plan, an RRSP or RRIF and
# their locked-in forms (Rev. Proc. 2014-55); a TFSA, RESP, RDSP or FHSA
# has no US deferral. taxjson-brokerage hands the set to the parsers
# (`plans_taxed`), whose taxable-versus-registered check then reads a
# broker's Account Type naming one of them as a taxable account
# (backlog TJ-0613: a US project's TFSA beside its margin account).
PLANS_TAXED_AS_ORDINARY: Dict[str, Tuple[str, ...]] = {
    CANADA: (),
    USA: ("tfsa", "resp", "rdsp", "fhsa"),
}


def stablecoin_model(country: Optional[str]) -> str:
    """The basis USD stablecoins are booked on: "cash" in a Canadian
    project (US-dollar cash, taxjson's stated approximation,
    CA-CRYPTO-02), "property" like any coin in a US one (US-CRYPTO-02).
    A missing or odd country is refused (`canonical_country`): the
    neutral basis of a parse without a country is `parse_basis`'s
    alone, so no step past the parse takes it by omission (backlog
    TJ-2608, TJ-2617)."""
    return "cash" if canonical_country(country) == CANADA else "property"


# What a parse without a country is told (`parse_basis`): property, no
# plan taxed as ordinary (the neutral answers, re-audit A2-0742,
# A2-1238; every plan a shelter is the plan guard's safe side).
NEUTRAL_BASIS: Dict[str, Any] = {"stablecoins_as_cash": False,
                                 "plans_taxed": frozenset()}


def parse_basis(country: Optional[str]) -> Dict[str, Any]:
    """What the country decides of a parse (parsers.settings
    `ParseSettings`, which takes it as given; `ParseSettings.for_country`
    builds one with it): the stablecoin model (`stablecoin_model`) and
    the registered plans taxed like an ordinary account
    (`plans_taxed_as_ordinary`); with no country (None or blank),
    NEUTRAL_BASIS. A basis, not a step after the parse: the two
    stablecoin models book different rows, count a row differently and
    refuse different shapes (the re-extraction's recommendation 8, its
    second slice)."""
    if country is None or (isinstance(country, str) and not country.strip()):
        return dict(NEUTRAL_BASIS)
    canon = canonical_country(country)
    return {"stablecoins_as_cash": stablecoin_model(canon) == "cash",
            "plans_taxed": plans_taxed_as_ordinary(canon)}


def marked_close_decides(country: Optional[str]) -> bool:
    """Whether the books read a row by the broker's CLOSE mark: Canada (a
    row marked CLOSE never opens a position, CA-OPT-12, CA-ACB-11). The
    US books go by position (backlog TJ-5369). With no country (None or
    blank) the neutral answer, False: no country's reading is guessed."""
    if country is None or (isinstance(country, str) and not country.strip()):
        return False
    return canonical_country(country) == CANADA


def plans_taxed_as_ordinary(country: Optional[str]) -> FrozenSet[str]:
    """The Canadian registered plans (lower case) a project of `country`
    taxes like an ordinary account; empty for Canada and when the
    country is not given (every plan a shelter: the guard's safe side)."""
    if country is None:
        return frozenset()
    return frozenset(PLANS_TAXED_AS_ORDINARY[canonical_country(country)])


def foreign_roc_choice(country: Any, explicit: Any, *,
                       no_country: Optional[str] = None,
                       what: str = "[settings] country") -> str:
    """How an issuer-designated return of capital from a non-Canadian
    issuer (IB's label) is booked: "dividend" (ITA s.90(1), the Canadian
    default) or "acb", for a project of `country` whose `[settings]
    foreign_return_of_capital` is `explicit` (None or "": not said).
    Always "acb" in a US project: a US filer's nondividend distribution
    reduces basis (section 301(c)(2)). `no_country`: what a blank
    country gives when nothing is said (a bare taxjson-brokerage run);
    None: a missing country is an error, as everywhere. The rule of
    lib/country.py's `foreign_roc_mode`, which reads a project's
    settings; this is it on the two values alone, for characterize."""
    if no_country is not None and not str(country or "").strip():
        if explicit in (None, ""):
            return no_country
    elif canonical_country(country, what=what) == USA:
        return "acb"
    if explicit in (None, ""):
        return "dividend"
    if explicit not in ("dividend", "acb"):
        raise CountryError(
            f"[settings] foreign_return_of_capital must be \"dividend\" "
            f"or \"acb\" (got {explicit!r})")
    return str(explicit)


def check_engine_allowed(country: str) -> None:
    """Raise when a test marker restricted this process to the other
    country's engine (see module doc). A no-op outside the tests."""
    allowed = os.environ.get(ENGINE_GUARD_ENV)
    if not allowed or allowed not in COUNTRIES:
        return
    c = canonical_country(country)
    if c != allowed:
        raise AssertionError(
            f"a test tagged with {DISPLAY_NAME[allowed]} tax rules ran the "
            f"{DISPLAY_NAME[c]} engine — tag it @rule_absent(..., "
            f"country=\"{c}\") if running both countries is the point")
