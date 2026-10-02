"""The account-type, account-name and quoted-boolean checks every config
reader applies.

Every filing command partitions accounts with an exact match on
`type == "taxable"` / `"sheltered"`. A typo'd or missing type matches
neither, so the account silently vanished from estimate, instalments,
`sum` FOR THE RETURN, form-export and close-year, while only `taxjson
run` (through validate_config) refused the same config (R1-268). This
is the one home for that check, so the run, every read-only command
and the web UI refuse the same configs with the same message.
"""
import difflib
import re
from typing import Any, Dict, List

ACCOUNT_TYPES = ("taxable", "sheltered")

# Every per-account artifact is work/<name>_<suffix>.json, so an account
# named `<other>_raw` owns `<other>_raw_base.json` — the other account's
# native books — and the gains discovery skips any `*_raw_gains.json`
# as a derivative: the account's gains vanished from sum/estimate, or
# overwrote a sibling's (2026-09 audit S022-00, S041-14). A name ending
# in one of these, or the combined book's own name, is refused.
RESERVED_NAME_SUFFIXES = ("_raw", "_base", "_gains", "_wash", "_merged",
                          "_sorted", "_filled", "_mapped", "_report",
                          "_tt", "_manifest", "_sources")
RESERVED_NAMES = ("sheltered",)
# Account names build file and directory names (inputs/<name>,
# work/<name>_*, reports/<name>_holdings.toml) and sub-tool argv: a
# "../x" name reads or writes outside the project, a "-x" name parses
# as a flag. load_config refused these, the web loader did not (R1-349).
ACCOUNT_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def account_name_problem(name: str) -> str:
    """A message when `name` would collide with a pipeline artifact
    name, else ''."""
    n = str(name)
    low = n.lower()
    if not ACCOUNT_NAME_RE.fullmatch(n):
        return (f"[accounts.{n!r}] is not a valid account name — use "
                f"letters, digits, '_', '-' or '.' (must start with a "
                f"letter, digit or '_'); it becomes file and directory "
                f"names.")
    if n.upper() == "COMBINED":
        return (f"[accounts.{n}]: the name COMBINED is reserved for the "
                f"cross-account wash radar — rename the account (and its "
                f"inputs/{n}/ folder).")
    if low in RESERVED_NAMES:
        return (f"[accounts.{n}]: {n!r} is reserved (work/{n}_base.json "
                f"is the combined registered-account book) — rename the "
                f"account (and its inputs/{n}/ folder)")
    for suf in RESERVED_NAME_SUFFIXES:
        if low.endswith(suf):
            return (f"[accounts.{n}]: an account name may not end in "
                    f"{suf!r} — work/{n}_*.json would collide with the "
                    f"pipeline's per-account {suf.lstrip('_')} artifacts "
                    f"(one account's books overwrite or hide another's). "
                    f"Rename the account (and its inputs/{n}/ folder), "
                    f"e.g. {n[:-len(suf)] + suf.replace('_', '-')!r}")
    return ""


# The per-account artifact namespaces an account `a` owns besides its
# fixed suffixes: work/a_tt_<stem>.json (a converted .tt), work/a_<broker>
# [_corp|_transfers].json (a broker parse). An account named
# `a_tt_x` or `a_questrade` lives inside `a`'s namespace: the two
# overwrote each other's books, and one run's stale-file cleanup deleted
# the other's corp and transfer files (re-audit A2-0140, A2-0402).
ARTIFACT_NAMESPACES = ("tt", "questrade", "ib", "webull", "rbc_direct",
                       "coinbase", "kraken", "generic")


def account_pair_problems(names) -> List[str]:
    """One message per account whose name sits in another account's
    artifact namespace (`<other>_<namespace>...`)."""
    out: List[str] = []
    names = [str(n) for n in names]
    for long in names:
        for short in names:
            if long == short or not long.lower().startswith(
                    short.lower() + "_"):
                continue
            rest = long[len(short) + 1:].lower()
            ns = next((x for x in ARTIFACT_NAMESPACES
                       if re.match(rf"{re.escape(x)}(?:$|[_-])", rest)),
                      None)
            if ns:
                out.append(
                    f"[accounts.{long}]: the name is inside account "
                    f"{short!r}'s work files (work/{short}_{ns}...json, "
                    f"its {'.tt' if ns == 'tt' else ns} books) — the two "
                    f"accounts would overwrite or delete each other's "
                    f"books. Rename it (and its inputs/{long}/ folder), "
                    f"e.g. {short + '-' + long[len(short) + 1:]!r}.")
                break
    return out


def account_type_problems(cfg: Dict[str, Any]) -> List[str]:
    """One message per [accounts.*] entry that is not a table or whose
    `type` is missing or not exactly "taxable"/"sheltered". Empty on a
    valid config."""
    out: List[str] = []
    accounts = cfg.get("accounts") or {}
    if not isinstance(accounts, dict):
        return ["[accounts] must be a table of [accounts.<name>] sections"]
    for name, acfg in accounts.items():
        clash = account_name_problem(name)
        if clash:
            out.append(clash)
            continue
        if not isinstance(acfg, dict):
            out.append(f"[accounts.{name}] must be a table")
            continue
        atype = acfg.get("type")
        if atype is None:
            # The old "default to sheltered" silently dropped an
            # untyped TAXABLE account's gains from every filing
            # command (2026-09 CLI audit).
            out.append(
                f"[accounts.{name}] has no `type` — it is required: "
                f"add type = \"taxable\" or type = \"sheltered\" "
                f"(taxable | sheltered). An untyped account would "
                f"otherwise be left out of the return.")
        elif atype not in ACCOUNT_TYPES:
            close = difflib.get_close_matches(str(atype).lower(),
                                              ACCOUNT_TYPES, n=1)
            hint = f" (did you mean {close[0]!r}?)" if close else ""
            out.append(
                f"[accounts.{name}] type must be \"taxable\" or "
                f"\"sheltered\", got {atype!r} — this account would "
                f"otherwise be silently dropped from the run{hint}")
        if "crypto" in acfg and not isinstance(acfg["crypto"], bool):
            # Every reader tests `crypto` for truthiness: a quoted
            # "false" moved a whole equity book to Schedule 3's
            # crypto-asset line and its dividends to staking income
            # (S005-00).
            out.append(
                f"[accounts.{name}] crypto must be true or false (no "
                f"quotes), got {acfg['crypto']!r}")
    return (out + account_pair_problems(accounts.keys())
            + bool_setting_problems(cfg))


def bool_setting_problems(cfg: Dict[str, Any]) -> List[str]:
    """A quoted boolean (`= "false"`) is truthy: every command but `run`
    read [settings] option_buyback_loss_superficial = "false" as ON
    (audit S021-07 / S076-17 — carryover, check-filed, audit and
    close-year applied the strict buy-back rule). Refused by every
    config reader, like `run` does. (accounts.*.crypto: above.)"""
    out: List[str] = []
    settings = cfg.get("settings") or {}
    if isinstance(settings, dict):
        # fx_cash_gains = "false" switched the FX-on-cash report ON and
        # its banner said "= true" (R1-152, R1-261).
        for key in ("option_buyback_loss_superficial", "fx_cash_gains"):
            v = settings.get(key)
            if v is not None and not isinstance(v, bool):
                out.append(f"[settings] {key} must be true or false, "
                           f"unquoted (got {v!r})")
        # Only `run` checked these: option-boundary read a typo'd
        # "grants" as close timing and advised enabling grant timing,
        # close-year wrote the typo into the filed-year lock, and a
        # since of `true` read as year 1 (R1-183).
        opt = settings.get("option_premium_timing")
        if opt is not None and (not isinstance(opt, str)
                                or opt.strip().lower()
                                not in ("grant", "close")):
            out.append(f"[settings] option_premium_timing must be "
                       f"\"grant\" or \"close\" (got {opt!r})")
        since = settings.get("option_grant_timing_since")
        if since is not None and not (isinstance(since, int)
                                      and not isinstance(since, bool)
                                      and 1900 <= since <= 2100):
            out.append(f"[settings] option_grant_timing_since must be a "
                       f"tax year such as 2025, unquoted (got {since!r})")
    return out


def settings_problems(cfg: Dict[str, Any]) -> List[str]:
    """Canonicalise [settings] in place and return what is wrong with
    it: the country (required; one spelling table, lib/country), the
    date basis, the base currency's spelling, and every setting or
    table the project's country does not own (lib/country
    SETTING_COUNTRY / CONFIG_COUNTRY), including a base currency that
    is not the country's. The one home for these checks: `taxjson`'s
    config readers die on the list, the web UI raises it. Stops at the
    first country problem (the ownership checks need a country)."""
    from taxjson.lib.country import (CountryError, config_country_problems,
                                     settings_country, TAX_DATES)
    settings = cfg.get("settings")
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        return ["[settings] must be a table"]
    try:
        settings["country"] = settings_country(settings)
        if "settings" not in cfg:
            cfg["settings"] = settings
    except CountryError as e:
        return [str(e)]
    out: List[str] = []
    tax_date = settings.get("tax_date")
    if tax_date is not None and tax_date not in TAX_DATES:
        out.append(f"[settings] tax_date must be settle|trade, "
                   f"got {tax_date!r}")
    # base_currency: " CAD" / "Cad " passed validation and failed later
    # as a misdiagnosed "rows still carry a non-CAD currency" error
    # (R1-153). One canonical, checked spelling for every reader.
    import re
    base = settings.get("base_currency")
    if base is not None:
        if not isinstance(base, str) \
                or not re.fullmatch(r"[A-Za-z]{3}", base.strip()):
            out.append(f"[settings] base_currency must be a 3-letter "
                       f"currency code such as \"CAD\" or \"USD\", got "
                       f"{base!r}")
            return out
        settings["base_currency"] = base.strip().upper()
    # Every reader of the prior-year lock (run, handoff, the checklist)
    # refuses a non-path the same way: handoff and the checklist turned
    # a list into "no prior-year record at P/['../2023/...']" (A2-1161).
    pyr = settings.get("prior_year_record")
    if pyr is not None and not (isinstance(pyr, str) and pyr.strip()):
        out.append(f"[settings] prior_year_record must be a path string "
                   f"such as \"../2024/filed/2024.json\" (got {pyr!r})")
    tz = settings.get("local_timezone")
    if tz is not None:
        from taxjson.lib.brokerages._crypto_common import utc_to_local
        from datetime import datetime as _dt
        try:
            if not isinstance(tz, str) or not tz.strip():
                raise ValueError
            utc_to_local(_dt(2025, 1, 1), tz.strip())
            settings["local_timezone"] = tz.strip()
        except ValueError:
            out.append(f"[settings] local_timezone must be an IANA zone "
                       f"name such as \"America/Toronto\" or "
                       f"\"America/Los_Angeles\", got {tz!r}")
    return out + config_country_problems(cfg)
