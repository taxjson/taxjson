"""The ONE country resolver and the country-ownership tables.

Canadian and US tax law must never mix. Every reader of a project's
country goes through this module, so a missing or odd `country` is
refused with the same message everywhere instead of quietly becoming
Canada in one command and an error in another (partition audit R1).

API (import from here; do not re-derive):

- ``canonical_country(value)`` -> ``"canada"`` | ``"usa"``. Accepts only
  the spellings in ``ALIASES`` (any case, surrounding blanks ignored).
  Anything else, and a missing value, raises ``CountryError`` (a
  ``ValueError``) with one message.
- ``settings_country(settings)`` -> the canonical country of a
  ``[settings]`` table; missing is an error.
- ``country_arg`` -> an ``argparse`` ``type=`` for ``--country``.
- ``is_usa(value)`` / ``is_canada(value)`` -> strict booleans (an
  unknown value raises, it is never "not usa, so Canada").
- ``default_tax_date(country)`` / ``resolve_tax_date(country, tax_date)``
  / ``settings_tax_date(settings)`` -> ``"settle"`` | ``"trade"``: the
  explicit value when given (checked), else the country default (Canada
  settles, the US trades).
- ``home_currency(country)`` -> ``"CAD"`` | ``"USD"``: a return is filed
  in it, so ``[settings] base_currency`` must equal it
  (``base_currency_problem``).

Ownership tables (who a setting, a CLI flag or a command belongs to):

- ``SETTING_COUNTRY``: every ``[settings]`` key -> ``"canada"``,
  ``"usa"`` or ``BOTH``. This is also the list of known keys.
- ``CONFIG_COUNTRY``: other config paths (``"[instalments]"``,
  ``"[estimate] deductions"`` ...) that belong to one country.
- ``PLAN_COUNTRY``: ``[accounts.X] plan`` kinds (tfsa ... Canada; ira,
  roth, 401k, hsa ... US; taxable / sheltered both); ``plan_kinds()``.
- ``FLAG_COUNTRY``: engine CLI flags that belong to one country
  (``--option-premium-timing`` ... Canada; ``--per-account-basis`` US).
- ``COMMAND_COUNTRY``: ``taxjson`` subcommands, or ``command:variant``
  (``form-export:8949``, ``crypto-sends:gift``), that belong to one
  country. ``taxjson``'s dispatch refuses the other country's commands
  before they run.
- ``PROJECT_FILE_COUNTRY``: project-root input files that belong to one
  country (``capital_gains_dividends.map``: Canada).

Checks built on the tables (each returns messages; the caller dies):

- ``config_country_problems(cfg)``: settings/tables owned by the other
  country, and a base currency that is not the country's.
- ``flag_country_problems(country, given, tool=...)``.
- ``command_country_problem(command, country, variant=None)``.
- ``project_file_problems(root, country)``.

- ``FLAG_VALUE_COUNTRY``: one value of a two-country flag that belongs
  to one country (``--foreign-roc dividend``: Canada).

When a fix adds a one-country setting, flag or command, add it to the
table here (with a ``*_WHY`` reason and, in tax-logic, the rule that
states it); ``scripts/check_tax_rules.py`` checks the tables stay
complete: every entry has a valid owner and a reason, every flag is a
real CLI option that ``refuse_foreign_flags`` reads, every command is a
``taxjson`` subcommand, every ``[settings]`` key the code reads is in
``SETTING_COUNTRY``, and every CLI option whose help calls itself
"Canada only" / "US only" is in a flag table.

Test hook: ``check_engine_allowed(country)`` is called by both gains
engines. When the environment variable ``TAXJSON_TEST_ENGINE_COUNTRY``
is set (only the test markers in ``tests/tax_rules`` set it), running
the other country's engine raises ``AssertionError`` — a test tagged for
a Canadian rule cannot silently exercise the US engine, in-process or
in a ``taxjson`` subprocess.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

CANADA = "canada"
USA = "usa"
COUNTRIES = (CANADA, USA)
BOTH = "both"

# The only accepted spellings (compared after strip().lower()).
ALIASES: Dict[str, str] = {"canada": CANADA, "ca": CANADA,
                           "usa": USA, "us": USA}

DISPLAY_NAME = {CANADA: "Canada", USA: "United States"}
HOME_CURRENCY = {CANADA: "CAD", USA: "USD"}
DEFAULT_TAX_DATE = {CANADA: "settle", USA: "trade"}
TAX_DATES = ("settle", "trade")

# Report wording owned by each country: a report never names the other
# country's form, cost term or loss-denial rule (re-audit A2-0735). The
# None entry is a standalone tool run without --country: neutral words.
GAINS_FORM = {CANADA: "Schedule 3", USA: "Form 8949",
              None: "the return's capital-gains form"}
COST_TERM = {CANADA: "ACB", USA: "basis", None: "cost"}
LOSS_RULE = {CANADA: "superficial-loss", USA: "wash-sale",
             None: "loss-denial"}

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


def settings_country(settings: Optional[Mapping[str, Any]], *,
                     what: str = "[settings] country") -> str:
    """The canonical country of a [settings] table (missing: error)."""
    return canonical_country((settings or {}).get("country"), what=what)


def country_arg(value: str) -> str:
    """argparse `type=` for --country: canonical, or a usage error."""
    import argparse
    try:
        return canonical_country(value, what="--country")
    except CountryError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def is_usa(value: Any) -> bool:
    return canonical_country(value) == USA


def is_canada(value: Any) -> bool:
    return canonical_country(value) == CANADA


def other_country(country: str) -> str:
    return USA if canonical_country(country) == CANADA else CANADA


def display_name(country: str) -> str:
    return DISPLAY_NAME[canonical_country(country)]


def home_currency(country: str) -> str:
    return HOME_CURRENCY[canonical_country(country)]


# Whether a taxpayer's identical property is ONE pool across all their
# taxable accounts: Canada averages the ACB across them (ITA s.47 — a
# move between two of your own accounts changes nothing); the US keeps
# lots per account (US-BASIS-01), so such a move must carry the lot.
BASIS_POOLED_ACROSS_ACCOUNTS = {CANADA: True, USA: False}


def basis_pooled_across_accounts(country: str) -> bool:
    return BASIS_POOLED_ACROSS_ACCOUNTS[canonical_country(country)]


def default_tax_date(country: str) -> str:
    """CRA dates a disposition by settlement, the IRS by trade date."""
    return DEFAULT_TAX_DATE[canonical_country(country)]


def resolve_tax_date(country: str, tax_date: Any = None, *,
                     what: str = "[settings] tax_date") -> str:
    """The date basis in force: an explicit settle|trade (exact
    spelling, as every config reader requires) else the country
    default."""
    if tax_date in (None, ""):
        return default_tax_date(country)
    if tax_date not in TAX_DATES:
        raise CountryError(f"{what} must be settle|trade, got {tax_date!r}")
    return str(tax_date)


def settings_tax_date(settings: Mapping[str, Any]) -> str:
    return resolve_tax_date(settings_country(settings),
                            settings.get("tax_date"))


# ------------------------------------------------------------ setting resolvers
# The engine and `taxjson tax-logic` read these settings through the SAME
# functions, so the rendered rule can never drift from what runs
# (partition audit SPEC-12). Option-premium timing: lib/pipeline
# option_timing_from_settings.

def foreign_roc_mode(settings: Mapping[str, Any]) -> str:
    """How an issuer-designated return of capital from a non-Canadian
    issuer is booked by the IB parser: "dividend" (ITA s.90(1), the
    Canadian default) or "acb". Always "acb" in a US project — a US
    filer's nondividend distribution reduces basis (§301(c)(2)); the
    key is Canada-only and refused there by the config readers."""
    if settings_country(settings) == USA:
        return "acb"
    explicit = settings.get("foreign_return_of_capital")
    if explicit in (None, ""):
        return "dividend"
    if explicit not in ("dividend", "acb"):
        raise CountryError(
            f"[settings] foreign_return_of_capital must be \"dividend\" "
            f"or \"acb\" (got {explicit!r})")
    return str(explicit)


FUTURES_SETTLE = ("trade", "next_day")


def futures_settle_mode(settings: Mapping[str, Any]) -> str:
    """[settings] futures_settle: "trade" (default) or "next_day"."""
    v = settings.get("futures_settle")
    if v in (None, ""):
        return "trade"
    if v not in FUTURES_SETTLE:
        raise CountryError(f"[settings] futures_settle must be \"trade\" "
                           f"or \"next_day\" (got {v!r})")
    return str(v)


# ------------------------------------------------------------ ownership

# Every [settings] key taxjson reads, and whose law it is. BOTH: the key
# means the same thing in either country. A key owned by one country is
# refused in a project of the other (it would be silently ignored, or
# worse, honoured: foreign_return_of_capital = "dividend" is ITA
# s.90(1), which a US filer must never get).
SETTING_COUNTRY: Dict[str, str] = {
    "year": BOTH,
    "country": BOTH,
    "base_currency": BOTH,
    "tax_date": BOTH,
    "source_currencies": BOTH,
    "cross_asset": BOTH,            # retired; warned and ignored
    "fx_cash_gains": BOTH,          # s.39(1.1) in Canada, §988 in the US
    "futures_settle": BOTH,
    "prior_year_record": BOTH,
    "local_timezone": BOTH,         # the zone crypto UTC stamps are dated in
    "province": CANADA,
    "option_premium_timing": CANADA,
    "option_grant_timing_since": CANADA,
    "option_buyback_loss_superficial": CANADA,
    "foreign_return_of_capital": CANADA,
    "corporate_distributions": CANADA,
    "ric_january_dividends": USA,
}

SETTING_WHY: Dict[str, str] = {
    "province": "provincial tax in the Canadian estimate",
    "option_premium_timing": "ITA s.49(1) grant timing; US premiums are "
                             "taxed at the close (§1234)",
    "option_grant_timing_since": "ITA s.49(1) grant timing; US premiums "
                                 "are taxed at the close (§1234)",
    "option_buyback_loss_superficial": "the ITA s.54 superficial-loss "
                                       "rule; the US has §1091",
    "foreign_return_of_capital": "ITA s.90(1) (a foreign issuer's return "
                                 "of capital is a dividend); in the US "
                                 "it reduces basis (§301(c)(2))",
    "corporate_distributions": "ITA s.104(13): which Canadian "
                               "\"distributions\" are a corporation's "
                               "(dated when paid) rather than a trust's",
    "ric_january_dividends": "IRC §852(b)(7) / §857(b)(9) January "
                             "dividends received on Dec 31",
}

# Config paths outside [settings] owned by one country.
CONFIG_COUNTRY: Dict[str, str] = {
    "[instalments]": CANADA,
    "[estimate] deductions": CANADA,
    "[estimate] carrying_charges": CANADA,
}

CONFIG_WHY: Dict[str, str] = {
    "[instalments]": "CRA instalments (ITA s.156); US estimated tax is "
                     "not modelled",
    "[estimate] deductions": "lines 20700-23500 of the Canadian return",
    "[estimate] carrying_charges": "line 22100 of the Canadian return",
}

# [accounts.X] plan kinds: each registered plan belongs to one country
# (audit A2-0739, A2-1272, A2-1332); "taxable" and "sheltered" to both.
# The one table `taxjson`'s config check and its scan read.
PLAN_COUNTRY: Dict[str, str] = {
    "tfsa": CANADA, "rrsp": CANADA, "rrif": CANADA, "lira": CANADA,
    "lif": CANADA, "lrif": CANADA, "fhsa": CANADA, "resp": CANADA,
    "rdsp": CANADA, "prpp": CANADA,
    "ira": USA, "roth": USA, "401k": USA, "403b": USA, "457b": USA,
    "sep": USA, "hsa": USA, "529": USA,
    "taxable": BOTH, "sheltered": BOTH,
}

PLAN_WHY: Dict[str, str] = {
    CANADA: "a Canadian registered plan",
    USA: "a US tax-advantaged account",
}


def plan_kinds(country: Optional[str] = None) -> List[str]:
    """The [accounts.X] plan values a project of `country` accepts (all
    of them when None), in table order."""
    return [k for k, o in PLAN_COUNTRY.items()
            if country is None or o in (BOTH, country)]


# CLI flags owned by one country: the engine CLIs' (taxjson-gains,
# -explain, -audit, -carryover: refuse_foreign_flags) and `taxjson`'s
# estimate flags (sum/estimate/instalments: checked at dispatch).
FLAG_COUNTRY: Dict[str, str] = {
    "--option-premium-timing": CANADA,
    "--option-grant-since": CANADA,
    "--option-buyback-wash": CANADA,
    "--per-account-basis": USA,
    "--province": CANADA,
    "--deductions": CANADA,
    "--carrying-charges": CANADA,
    "--corporate-distribution": CANADA,
    "--ric-january-dividend": USA,
    "--slip-gains": CANADA,
}

FLAG_WHY: Dict[str, str] = {
    "--option-premium-timing": "ITA s.49(1)",
    "--option-grant-since": "ITA s.49(1)",
    "--option-buyback-wash": "ITA s.54",
    "--per-account-basis": "US FIFO basis per account; Canada pools "
                           "identical property across accounts (s.47)",
    "--province": "provincial tax in the Canadian estimate",
    "--deductions": "lines 20700-23500 of the Canadian return",
    "--carrying-charges": "line 22100 of the Canadian return",
    "--corporate-distribution": "ITA s.104(13) trust income dating",
    "--ric-january-dividend": "IRC §852(b)(7) / §857(b)(9)",
    "--slip-gains": "T5 box 18 capital-gains dividends (ITA s.130.1(4) / "
                    "s.131(1)); US fund capital-gain distributions are "
                    "not modelled",
}

# One VALUE of a two-country flag that belongs to one country: the flag
# itself means the same in both (how a foreign issuer's return of
# capital is booked), but "dividend" is ITA s.90(1) (re-audit A2-0719).
FLAG_VALUE_COUNTRY: Dict[tuple, str] = {
    ("--foreign-roc", "dividend"): CANADA,
}

FLAG_VALUE_WHY: Dict[tuple, str] = {
    ("--foreign-roc", "dividend"): "ITA s.90(1): a non-resident "
                                   "corporation's distribution is a "
                                   "dividend; a US filer's nondividend "
                                   "distribution lowers basis "
                                   "(§301(c)(2))",
}

# `taxjson` subcommands (or command:variant) owned by one country.
COMMAND_COUNTRY: Dict[str, str] = {
    "t1135": CANADA,
    "instalments": CANADA,
    "option-boundary": CANADA,
    "form-export:schedule3": CANADA,
    "form-export:8949": USA,
    "form-export:txf": USA,
    "crypto-sends:gift": CANADA,
}

COMMAND_WHY: Dict[str, str] = {
    "t1135": "Form T1135 is a Canadian form (ITA s.233.3)",
    "instalments": "CRA instalments (ITA s.156); US estimated tax is not "
                   "modelled",
    "option-boundary": "ITA s.49 premium timing; US premiums are taxed at "
                       "the close (§1234), so there is no year boundary "
                       "to amend",
    "form-export:schedule3": "Schedule 3 is a Canadian form; a US return "
                             "uses Form 8949 (--form 8949)",
    "form-export:8949": "Form 8949 is a US form; a Canadian return uses "
                        "Schedule 3 (--form schedule3)",
    "form-export:txf": "TXF is a US (TurboTax) format; a Canadian return "
                       "uses Schedule 3 (--form schedule3)",
    "crypto-sends:gift": "a gift is a disposition at fair value only in "
                         "Canada (ITA s.69(1)(b)); for a US donor it is "
                         "not a sale — record it as `self`",
}


# Project-root input files owned by one country. The views that read
# one refuse it in a project of the other country.
PROJECT_FILE_COUNTRY: Dict[str, str] = {
    "capital_gains_dividends.map": CANADA,
}

PROJECT_FILE_WHY: Dict[str, str] = {
    "capital_gains_dividends.map": "T5 box 18 capital-gains dividends "
                                   "(ITA s.130.1(4)/s.131(1), line "
                                   "17400); a US fund's capital-gain "
                                   "distributions (1099-DIV box 2a) are "
                                   "not modelled",
}


def project_file_problems(root: Any, country: str) -> List[str]:
    """One message per project-root file in `root` that `country` does
    not own (PROJECT_FILE_COUNTRY)."""
    from pathlib import Path
    out: List[str] = []
    for name, owner in PROJECT_FILE_COUNTRY.items():
        if owner not in (BOTH, country) and (Path(root) / name).is_file():
            out.append(_owner_problem(owner, country, name,
                                      PROJECT_FILE_WHY.get(name, "")))
    return out


def _owner_problem(owner: str, country: str, thing: str, why: str) -> str:
    return (f"{thing} is {DISPLAY_NAME[owner]}-only ({why}); this project "
            f"is country = \"{country}\" — remove it")


def base_currency_problem(country: str, base: Any) -> Optional[str]:
    """A message when `base` is set and is not the country's currency.
    A return is filed in its own currency, so every FOR THE RETURN
    figure (Schedule 3, Form 8949, the estimate's brackets) would be in
    the wrong one; the rate source (Bank of Canada for CAD) and the
    futures and stablecoin rules follow the base too."""
    if base in (None, ""):
        return None
    c = canonical_country(country)
    want = HOME_CURRENCY[c]
    if str(base).strip().upper() != want:
        return (f"[settings] base_currency is {base!r} but country is "
                f"{c} — a {DISPLAY_NAME[c]} return is filed in {want}; "
                f"set base_currency = \"{want}\"")
    return None


def config_country_problems(cfg: Mapping[str, Any]) -> List[str]:
    """Every config entry the project's country does not own, plus a
    base currency that is not the country's. Assumes [settings] country
    is valid (raises CountryError otherwise)."""
    settings = cfg.get("settings") or {}
    country = settings_country(settings)
    out: List[str] = []
    for key in settings:
        owner = SETTING_COUNTRY.get(key, BOTH)
        if owner not in (BOTH, country):
            out.append(_owner_problem(owner, country, f"[settings] {key}",
                                      SETTING_WHY.get(key, "")))
    for path, owner in CONFIG_COUNTRY.items():
        if owner in (BOTH, country):
            continue
        if path.startswith("[") and path.endswith("]"):
            present = path[1:-1] in cfg
        else:
            table, _, key = path.partition("] ")
            tbl = cfg.get(table.lstrip("["))
            present = isinstance(tbl, dict) and key in tbl
        if present:
            out.append(_owner_problem(owner, country, path,
                                      CONFIG_WHY.get(path, "")))
    accounts = cfg.get("accounts") or {}
    if isinstance(accounts, Mapping):
        for name, acfg in accounts.items():
            if not isinstance(acfg, Mapping) or acfg.get("plan") is None:
                continue
            plan = str(acfg.get("plan")).strip().lower()
            owner = PLAN_COUNTRY.get(plan, BOTH)
            if owner not in (BOTH, country):
                out.append(_owner_problem(
                    owner, country, f"[accounts.{name}] plan = \"{plan}\"",
                    PLAN_WHY[owner])
                    + f" (this country's plans: "
                      f"{' | '.join(plan_kinds(country))})")
    bp = base_currency_problem(country, settings.get("base_currency"))
    if bp:
        out.append(bp)
    return out


def flag_country_problems(country: str, given: Mapping[str, Any], *,
                          tool: str = "") -> List[str]:
    """One message per flag in `given` ({flag: value}) that was supplied
    (truthy / not None) but belongs to the other country."""
    c = canonical_country(country, what="--country")
    out = []
    for flag, val in given.items():
        if val in (None, False, "") or val == []:
            continue
        vkey = (flag, val) if isinstance(val, str) else None
        if vkey in FLAG_VALUE_COUNTRY:
            vowner = FLAG_VALUE_COUNTRY[vkey]
            if vowner != c:
                out.append(f"{tool + ': ' if tool else ''}{flag} {val} is "
                           f"{DISPLAY_NAME[vowner]}-only "
                           f"({FLAG_VALUE_WHY.get(vkey, '')}); it does "
                           f"not apply to country {c}")
            continue
        owner = FLAG_COUNTRY.get(flag)
        if owner is None or owner == c:
            continue
        out.append(f"{tool + ': ' if tool else ''}{flag} is "
                   f"{DISPLAY_NAME[owner]}-only ({FLAG_WHY.get(flag, '')}); "
                   f"it does not apply to country {c}")
    return out


def command_country(command: str, variant: Optional[str] = None
                    ) -> Optional[str]:
    """The country that owns `command` (or `command:variant`), else None."""
    if variant:
        owner = COMMAND_COUNTRY.get(f"{command}:{variant}")
        if owner:
            return owner
    return COMMAND_COUNTRY.get(command)


def command_country_problem(command: str, country: str,
                            variant: Optional[str] = None) -> Optional[str]:
    """A refusal message when `command` belongs to the other country."""
    c = canonical_country(country)
    owner = command_country(command, variant)
    if owner is None or owner == c:
        return None
    key = f"{command}:{variant}" if variant and \
        f"{command}:{variant}" in COMMAND_COUNTRY else command
    if key == command:
        shown = f"`taxjson {command}`"
    elif command == "form-export":
        shown = f"`taxjson form-export --form {variant}`"
    else:
        shown = f"`taxjson {command}` with {variant!r}"
    return (f"{shown} is {DISPLAY_NAME[owner]}-only "
            f"({COMMAND_WHY.get(key, '')}); this project is "
            f"country = \"{c}\"")


# ------------------------------------------------------------ test hook

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


def owners(table: Mapping[str, str], country: str) -> Iterable[str]:
    """Entries of an ownership table that belong to `country` only."""
    c = canonical_country(country)
    return [k for k, v in table.items() if v == c]


def add_country_argument(parser, *, help: str = "") -> None:
    """The one --country option for every standalone tool that applies
    one country's rules: REQUIRED (a missing country is never a silent
    Canada), canonicalised by country_arg. `taxjson` always passes it."""
    parser.add_argument(
        "--country", required=True, type=country_arg,
        metavar="{canada,ca,usa,us}",
        help=help or "Whose tax rules to apply (required): canada | usa")


_FLAG_ATTRS = {"--option-premium-timing": "option_premium_timing",
               "--option-grant-since": "option_grant_since",
               "--option-buyback-wash": "option_buyback_wash",
               "--per-account-basis": "per_account_basis",
               "--province": "province",
               "--deductions": "deductions",
               "--carrying-charges": "carrying_charges",
               "--corporate-distribution": "corporate_distribution",
               "--ric-january-dividend": "ric_january_dividend",
               "--slip-gains": "slip_gains",
               "--foreign-roc": "foreign_roc"}


def given_flags(args) -> Dict[str, Any]:
    """{flag: value} of the FLAG_COUNTRY / FLAG_VALUE_COUNTRY flags an
    argparse Namespace carries (absent attributes are None)."""
    return {flag: getattr(args, attr, None)
            for flag, attr in _FLAG_ATTRS.items()}


def refuse_foreign_flags(args, tool: str) -> None:
    """Exit 2 when an engine CLI was given a flag the --country does not
    own (FLAG_COUNTRY): they used to be dropped without a word, so a
    user who asked for s.49 grant timing on US books was never told it
    had no effect (partition ENGINE-03). Call BEFORE any default is
    filled into those attributes."""
    import sys
    problems = flag_country_problems(args.country, given_flags(args),
                                     tool=tool)
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        sys.exit(2)
