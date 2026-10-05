"""The taxjson.toml template: every key taxjson reads, documented, per country.

`taxjson init` writes it with the scaffold's values; `taxjson format`
re-renders an existing project's file into it with the user's values and
comments. One renderer serves both, so a fresh scaffold is already
formatted (formatting it is a no-op).

Layout. The tables come in a fixed order: [settings]; the accounts (a
commented `# [accounts.NAME]` reference block naming every account key,
then each [accounts.NAME] table in the user's order, then a commented
example of one more account); then [estimate], [carryover],
[[distributions]], and for Canada [[capital_gains_dividends]] and
[instalments]. Arrays of tables keep their entries' order. Inside every
table the keys are in alphabetical order, active and commented-out keys
interleaved in one sequence, and every key line of the table (`key` or
`# key`) is padded to the table's widest so the `=` signs form one column.
No line carries an end-of-line comment: a key's description is on the
line(s) just above it (plain `# text`, wrapped), and a key with a
description is separated from the one before by a blank line (keys
without one — an [[...]] entry's, or keys the template does not know —
follow each other directly). A key that is set is written active with its
value; a key that is not set is written commented out, `# key = default`
(or an example where it has none). A table that is absent is written
commented out. Only the keys the project's country owns are listed
(lib/country SETTING_COUNTRY / CONFIG_COUNTRY / PLAN_COUNTRY): a Canadian
file never mentions a US-only key and the other way round.

The key lists come from the validator (lib/config_check: ACCOUNT_KEYS,
ESTIMATE_KEYS, ... and lib/country.SETTING_COUNTRY);
tests/test_config_template.py fails when a key the validator accepts has
no entry here.

`format_config(text)` keeps everything the file says:

- every value (the parsed TOML of the result must equal the input's,
  checked before anything is returned; account order and the order of
  [[...]] entries are kept);
- keys the template does not know, at the end of their table under a
  "Not in the template" line, alphabetically (a top-level key at the top,
  a table at the end of the file);
- comments: a trailing comment on a key or table line moves to its own
  line just above that line (after the key's description); a comment
  block stays directly above the key or table line that follows it (a
  commented-out key counts: the block goes above that key's template
  line), and so moves with that key to its alphabetical place; a block
  that holds a commented-out key of your own (`# province = "BC"`) goes
  to that key; a block separated by a blank line from the next table goes
  to the end of the table it was in. Comment lines that are the
  template's own text — or text an earlier `taxjson init` wrote,
  recognised by hash — are regenerated rather than kept; a bare `#` line
  carries nothing and is dropped. A multi-line value with comments inside
  it is kept verbatim. Anything that cannot be placed goes to a "Your
  notes" block at the end of the file; nothing is dropped.
"""
from __future__ import annotations

import hashlib
import os
import re
import textwrap
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Set, Tuple, Union)

from taxjson.lib import country as C
from taxjson.lib.config_check import (ACCOUNT_KEYS, CARRYOVER_KEYS, CGD_KEYS,
                                      DISTRIBUTION_KEYS, ESTIMATE_KEYS,
                                      INSTALMENTS_KEYS, RETIRED_SETTINGS)
from taxjson.lib.tomlcompat import tomllib

# Descriptions are wrapped to this many characters after the "# ".
DOC_WIDTH = 76

NOTES_HEADING = ("# Your notes (kept by tjs format) — comments it could "
                 "not attach to a setting:")
_UNKNOWN_KEYS_LINE = ("# Not in the template (kept by tjs format; taxjson "
                      "does not read these — check the spelling):")
_UNKNOWN_TOP_LINE = ("# Not in the template (kept by tjs format): "
                     "top-level keys taxjson does not read.")
_UNKNOWN_TABLES_LINE = ("# Not in the template (kept by tjs format): "
                        "tables taxjson does not read.")


class FormatError(ValueError):
    """The file cannot be formatted without losing something; str(e) is
    the user-facing reason."""


# ------------------------------------------------------------------ spec

Text = Union[str, Mapping[str, str]]


@dataclass(frozen=True)
class Key:
    """One documented key. `value` is the TOML text shown when the key
    is not set (its default, or an example where it has none) and may
    hold placeholders ({year}, {prev_year}, {next_year}, {home},
    {source}, {tax_date}, {tz}, {name}). `doc` is its description (a
    str, or {country: str}), written wrapped on the lines above it."""
    name: str
    value: Text
    doc: Text


def _pick(v: Any, country: str) -> Any:
    return v.get(country, v.get("*")) if isinstance(v, Mapping) else v


# The example zone a commented-out local_timezone line shows. NOT a
# default: there is none (a project with a crypto account must name its
# zone; `taxjson init` writes this machine's zone when it can read one).
_EXAMPLE_TZ = "America/New_York"


def _provinces() -> str:
    from taxjson.lib.tax_estimate import CA_PROVINCES
    return " | ".join(CA_PROVINCES)


def _plans(country: str) -> str:
    return " | ".join(k for k in C.plan_kinds(country)
                      if C.PLAN_COUNTRY[k] != C.BOTH)


def _bases() -> str:
    from taxjson.bin.taxjson_instalments import BASES
    return " | ".join(BASES)


_FILE_HEADER = (
    "# taxjson configuration — https://github.com/taxjson/taxjson",
    "#",
    "# Every key taxjson reads is listed here, alphabetically within each",
    "# table and with one `=` column per table, so year-over-year projects",
    "# diff cleanly:",
    "#   diff ~/taxes/{prev_year}/taxjson.toml ~/taxes/{year}/taxjson.toml",
    "# Each key's description is on the lines above it. A commented key shows",
    "# its default (or an example where it has none); uncomment it to change",
    "# it. `taxjson format` puts an edited file back into this layout,",
    "# keeping your values and comments.",
)

# [settings]. A key the project's country does not own is left out
# (lib/country.SETTING_COUNTRY). Written in alphabetical order.
SETTINGS_SPEC: Tuple[Key, ...] = (
    Key("year", "{year}",
        "The tax year the pipeline reports on (required)."),
    Key("country", '"{country}"', "canada | ca | usa | us (required)."),
    Key("province", '"ON"',
        "{provinces}: the province `taxjson estimate` taxes at. No "
        "default."),
    Key("base_currency", '"{home}"',
        {"canada": "Report currency: CAD (Bank of Canada rates).",
         "usa": "Report currency: USD."}),
    Key("source_currencies", {"canada": '["USD"]', "usa": '["CAD"]'},
        {"canada": "Currencies you hold besides base_currency (their FX "
                   "rates are fetched). Default [\"USD\"].",
         "usa": "Currencies you hold besides USD (their FX rates are "
                "fetched). Default none: an all-USD project needs nothing "
                "here; uncomment it only for an account or trade in "
                "another currency."}),
    Key("tax_date", '"{tax_date}"',
        {"canada": "settle | trade. Default settle: CRA dates a sale by "
                   "settlement.",
         "usa": "trade | settle. Default trade: the IRS dates a sale by "
                "trade date."}),
    Key("local_timezone", '"{tz}"',
        "The zone crypto UTC times are dated in (an IANA name). No "
        "default: required with a crypto account."),
    Key("prior_year_record", '"../{prev_year}/filed/{prev_year}.json"',
        "Last year's close-year record (`taxjson handoff`). No default."),
    Key("leaps_months", "9",
        "Options bought more than this many months before expiry count as "
        "LEAPS (the leaps and leaps-sum views only; no tax figure). "
        "Default 9."),
    Key("futures_settle", '"trade"',
        "trade | next_day. trade: futures and futures options settle on "
        "the TRADE date (daily variation margin); next_day: the clearing "
        "premium date."),
    Key("fx_cash_gains", "false",
        {"canada": "true: an end-of-run FX-on-cash report (s.39(1.1), $200 "
                   "de minimis).",
         "usa": "true: an end-of-run FX-on-cash report (§988, ordinary "
                "income)."}),
    Key("foreign_return_of_capital", '"dividend"',
        "A non-Canadian issuer's return of capital (IB): \"dividend\" "
        "(s.90(1), the default) | \"acb\"."),
    Key("corporate_distributions", '["XYZQ.TO"]',
        "Canadian issuers whose distributions are a corporation's, dated "
        "when paid (README \"Income dating\"). No default."),
    Key("ric_january_dividends", '["XYZQ.US {next_year}-01-30"]',
        "January fund/REIT dividends taxed as received Dec 31 (IRC "
        "§852(b)(7) / §857(b)(9)): \"SYMBOL\" (every January one) or "
        "\"SYMBOL YYYY-01-DD\" (that payment). No default."),
    Key("option_premium_timing", '"grant"',
        "\"grant\": a written option's premium is a gain in the year "
        "WRITTEN (ITA s.49(1)); \"close\": it is netted at the closing "
        "transaction instead. See `taxjson option-boundary`."),
    Key("option_grant_timing_since", "{year}",
        "Contracts written before this year keep close timing (default: "
        "`year`). SET ONCE to the first year you FILE under grant timing "
        "and keep it UNCHANGED in every later year's project (do not bump "
        "it with `year`)."),
    Key("option_buyback_loss_superficial", "false",
        "true: the strict s.54 reading — a loss on buying back a written "
        "option is superficial when identical options are bought within "
        "30 days and still held."),
)

ACCOUNT_SPEC: Tuple[Key, ...] = (
    Key("type", '"taxable"', "REQUIRED: taxable | sheltered."),
    Key("plan", {"canada": '"rrsp"', "usa": '"ira"'},
        "The plan when the name doesn't say (default: from the name): "
        "{plans}."),
    Key("crypto", "false",
        "true: a Coinbase / Kraken account (crypto prices are filled in by "
        "the run)."),
    Key("transfers", "false",
        "true: keep TRANSFER rows (contributions/withdrawals)."),
    Key("holdings", '["~/broker/{name}_holdings.toml"]',
        "Positions files `taxjson sanity` reconciles against. Default: "
        "none."),
    Key("combined_broker_accounts", "false",
        "true: every broker account in these statements is yours, taxable "
        "together."),
    Key("exercise_fee", "1.00",
        "Webull: the exercise/assignment charge on the stock leg. No "
        "default: without it no exercise/assignment is inferred (each "
        "candidate is named)."),
    Key("year_end_posting", '"06-30"',
        "RBC: the day next year by which year-end book-cost rows are "
        "posted. Default 06-30."),
    Key("brokerage", '"questrade"',
        "The `taxjson fetch` source (taxjson-fetch plugin): questrade | "
        "ibkr_flex."),
    Key("account", '"12345678"',
        "questrade: the account number `taxjson fetch` downloads."),
    Key("query_id", '"123456"',
        "ibkr_flex: the Flex query id `taxjson fetch` runs."),
)

_ACCOUNTS_DOC = (
    "One [accounts.NAME] table per folder under inputs/ (the folder name is "
    "the account name). Every key, with its default:")

# The scaffold's accounts and the commented "one more account" example.
SCAFFOLD_ACCOUNTS = {
    "canada": ("margin", "tfsa", "rrsp", "crypto"),
    "usa": ("margin", "roth", "401k", "crypto"),
}
_MORE_ACCOUNT = {
    "canada": ("lira", "More accounts: one table per inputs/ folder, e.g. a "
                       "locked-in retirement account:"),
    "usa": ("ira", "More accounts: one table per inputs/ folder, e.g. a "
                   "traditional IRA:"),
}


@dataclass(frozen=True)
class Table:
    name: str
    aot: bool                      # an array of tables ([[name]])
    doc: Text                      # the heading above the table line
    keys: Tuple[Key, ...]


# After [settings] and the accounts, in this order.
TABLES: Tuple[Table, ...] = (
    Table("estimate", False, {
        "canada": "Estimate inputs (`taxjson estimate`, and the instalments "
                  "current-year basis), used when the command-line flags "
                  "aren't given:",
        "usa": "Estimate inputs (`taxjson estimate`), used when the "
               "command-line flags aren't given:"}, (
        Key("other_income", "0",
            "Income besides these books (employment, interest ...)."),
        Key("other_losses", "0",
            {"canada": "Net capital losses of earlier years applied, in "
                       "FULL dollars.",
             "usa": "The SHORT-term capital loss carryover applied."}),
        Key("deductions", "0", "RRSP 20800, FHSA, RPP ... (in full under "
                               "AMT)."),
        Key("carrying_charges", "0", "Line 22100 (50% under AMT)."),
        Key("amt_carryover", "{{ {prev_year} = 1200.50 }}",
            "Minimum tax carryover by year of origin (T691). No default."),
        Key("long_term_losses", "0",
            "The long-term capital loss carryover (Schedule D line 14)."),
    )),
    Table("carryover", False,
          "Losses actually applied on filed returns (`taxjson carryover`), "
          "by year:", (
              Key("claimed", "{{ {prev_year} = 4000.00 }}",
                  "Net capital losses applied on each filed return. No "
                  "default."),
          )),
    Table("distributions", True,
          "Non-cash fund distributions (a reinvested capital-gains "
          "distribution, a late return-of-capital factor): `taxjson run` "
          "books each as a cost adjustment on the shares held on the "
          "record date. One table each:", (
              Key("symbol", {"canada": '"XYZQ.TO"', "usa": '"XYZQ.US"'},
                  "The books' symbol."),
              Key("record_date", "{year}-12-29", "The record date."),
              Key("per_share", "0.25",
                  "Per share, in the base currency; negative = return of "
                  "capital."),
          )),
    Table("capital_gains_dividends", True,
          "T5 box 18 capital-gains dividends the books carry as dividends "
          "(`taxjson divs-sum`, the estimate). One table per payment or "
          "year:", (
              Key("symbol", '"ABCX.TO"', "The books' symbol."),
              Key("year", "{year}",
                  "Every dividend of that tax year (instead of date)."),
              Key("date", "{year}-06-16",
                  "One payment's date (instead of year)."),
              Key("amount", '"all"',
                  "\"all\" or the box 18 amount, e.g. 1.25."),
              Key("account", '"margin"', "Default: the taxable accounts."),
          )),
    Table("instalments", False,
          "Tax instalments (`taxjson instalments`, and a summary inside "
          "`taxjson estimate`). Uncomment and fill in YOUR figures.", (
              Key("basis", '"current_year"', "{bases}."),
              Key("withheld", "0", "Tax withheld at source this year."),
              Key("prior_year_net_tax", "55000",
                  "Last year's net tax owing (no default), both years as "
                  "CRA's instalment chart defines it: lines 42000 + 42200 + "
                  "42800 (+ 43200) minus 43700 and the refundable credits "
                  "— NOT line 48500. Supply BOTH even on current_year: a 0 "
                  "reads as \"I owed nothing\"."),
              Key("second_prior_net_tax", "41000",
                  "The year before's net tax owing. No default."),
              Key("prescribed_rate", "0.07",
                  "CRA's overdue-tax rate. Default: CRA's published "
                  "rates."),
              Key("prescribed_rates",
                  "[\n  { from = \"{year}-01-01\", rate = 0.08 },\n"
                  "  { from = \"{year}-07-01\", rate = 0.07 },\n]",
                  "Or a dated schedule: CRA resets the rate quarterly."),
              Key("paid",
                  "[\n  { date = \"{year}-03-16\", amount = 15000 },\n"
                  "  { date = \"{year}-05-20\", amount = 12000, "
                  "note = \"refund transferred\" },\n]",
                  "Instalments paid. No default."),
          )),
)

TABLE_BY_NAME = {t.name: t for t in TABLES}


def _sorted_keys(keys: Iterable[Key]) -> List[Key]:
    return sorted(keys, key=lambda k: k.name)


# ------------------------------------------------------------ ownership

def key_owner(table: str, key: Optional[str] = None) -> str:
    """The country that owns a config table or key (lib/country tables),
    or BOTH."""
    if table == "settings":
        return C.SETTING_COUNTRY.get(key, C.BOTH) if key else C.BOTH
    owner = C.CONFIG_COUNTRY.get(f"[{table}]")
    if owner:
        return owner
    if key:
        return C.CONFIG_COUNTRY.get(f"[{table}] {key}", C.BOTH)
    return C.BOTH


def owned(country: str, table: str, key: Optional[str] = None) -> bool:
    return key_owner(table, key) in (C.BOTH, country)


def validator_keys() -> Dict[str, Tuple[str, ...]]:
    """{table: keys} the validator accepts (lib/config_check,
    lib/country.SETTING_COUNTRY), retired settings excluded."""
    return {
        "settings": tuple(k for k in C.SETTING_COUNTRY
                          if k not in RETIRED_SETTINGS),
        "accounts": ACCOUNT_KEYS,
        "estimate": ESTIMATE_KEYS,
        "instalments": INSTALMENTS_KEYS,
        "carryover": CARRYOVER_KEYS,
        "distributions": DISTRIBUTION_KEYS,
        "capital_gains_dividends": CGD_KEYS,
    }


def spec_keys() -> Dict[str, Tuple[str, ...]]:
    """{table: keys} the template documents (every country)."""
    out = {"settings": tuple(k.name for k in SETTINGS_SPEC),
           "accounts": tuple(k.name for k in ACCOUNT_SPEC)}
    for t in TABLES:
        out[t.name] = tuple(k.name for k in t.keys)
    return out


# ------------------------------------------------------------ timezone

def system_timezone() -> Optional[str]:
    """This machine's IANA zone name (TZ, /etc/timezone, the
    /etc/localtime link), or None when none can be read or it is not a
    zone name taxjson accepts."""
    cands: List[str] = []
    env = (os.environ.get("TZ") or "").strip().lstrip(":")
    if env:
        cands.append(env)
    try:
        with open("/etc/timezone", encoding="utf-8") as f:
            cands.append(f.readline().strip())
    except OSError:
        pass
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            cands.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in cands:
        if name in _SERVER_DEFAULT_ZONES:
            # Servers, containers and WSL run on UTC whatever the user's
            # zone is: leave the key commented rather than date crypto
            # trades in UTC without the user choosing it.
            return None
        if valid_timezone(name):
            return name
    return None


_SERVER_DEFAULT_ZONES = frozenset({
    "UTC", "Etc/UTC", "Etc/UCT", "UCT", "Etc/Universal", "Universal",
    "Etc/Zulu", "Zulu", "GMT", "Etc/GMT", "Etc/GMT0", "Etc/GMT+0",
    "Etc/GMT-0", "Etc/Greenwich", "Greenwich", "GMT0"})


def valid_timezone(name: str) -> bool:
    """An IANA Area/Location name (or UTC) the crypto dating accepts."""
    if not name or ("/" not in name and name != "UTC") \
            or name.startswith(("/", "posix/", "right/")):
        return False
    try:
        from taxjson.lib.brokerages._crypto_common import utc_to_local
        utc_to_local(_datetime(2025, 1, 1), name)
    except Exception:  # noqa: BLE001 — any failure: not a usable zone
        return False
    return True


# ------------------------------------------------------------ TOML values

_BARE = re.compile(r"[A-Za-z0-9_-]+\Z")


def key_repr(key: str) -> str:
    """A TOML key: bare when it can be, else a quoted string."""
    return key if _BARE.match(key) else toml_string(key)


def toml_string(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\u{o:04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def toml_value(v: Any, *, top: bool = False) -> str:
    """Canonical TOML text of a parsed value: strings quoted, dates as
    dates, inline tables `{ k = v }`. At the top level (`top`) a list of
    tables, or a list too long for one line, is written one element per
    line."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return toml_string(v)
    if isinstance(v, (_datetime, _date, _time)):
        return v.isoformat()
    if isinstance(v, list):
        items = [toml_value(x) for x in v]
        one = "[" + ", ".join(items) + "]"
        if top and v and (any(isinstance(x, dict) for x in v)
                          or len(one) > 60):
            return "[\n" + "".join(f"  {i},\n" for i in items) + "]"
        return one
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{key_repr(str(k))} = {toml_value(x)}"
                                for k, x in v.items()) + " }"
    raise FormatError(f"cannot write a {type(v).__name__} value as TOML")




# ------------------------------------------------------------ comment text

def _norm(text: str) -> str:
    """Comment text compared for "is this the template's own line":
    leading '#'s and blanks dropped, years generic, blanks collapsed."""
    t = re.sub(r"^[#\s]+", "", text.strip())
    t = re.sub(r"\b(?:19|20)\d\d\b", "YYYY", t)
    return re.sub(r"\s+", " ", t).strip()


def _hash(norm: str) -> str:
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


# Comment lines (and trailing comments) earlier `taxjson init` versions
# wrote, as hashes of their _norm text (sha256, first 16 hex digits; the
# text of every '#' onward of every line each earlier template rendered,
# both countries; the last block is the grouped, end-of-line-comment
# layout before the alphabetical one): `taxjson format` regenerates them as template text
# instead of keeping them as the user's notes. Hashes, not text: the old
# examples are not carried in the source.
_LEGACY_TEMPLATE_HASHES = frozenset("""
e7866667d98a969c 8210ac89894ed17f
001abf630b6115d0 002632fa29f5bc3d 00b37cf395b3dd0b 02add596de115d47
03d445a98351bec2 06af278016053226 084cbf481f008f28 0ea1fe5c78966f11
1048885439f16fe2 10899c5c906ee14f 10ea6239ba4c1830 10f8df2f5b5ea99d
13378f93af5a5f62 141110e520258ce6 146744308375b37b 151a84258cb4a886
16d60adfa5ee1be5 189620d3143060a7 18f2cc618cb96a61 1d5fdaa8389a353d
1d799cd4b98f03a9 1f0221eec7aebd36 213d535c3502c08a 239d50509acec98f
24088b27af93596a 2438a17b233c46a2 29bc8e35ab993de5 2a18e5c982575824
2c821f6573bf725e 2f0349417abc2af4 2f37252d6a962918 2f75a7dce9d6b3a0
30519547eb8d97cc 318961ee1304849e 3257f8a70a63aab0 32fc985b1a87f741
33536e8d94dfafc9 342257a193ac7d14 34a3d90a1f481192 3656d3e14b1180c9
3891e4db0d73ad80 3a3836c83244461e 3b7a2388cf228d8a 4080b425cad5fb08
417471684c6ff945 42db85f47a3de15e 46b9989ae54f5f74 485cc961457cc785
4902af48274b7854 4d9682207f263c31 4e87202b2d15f436 4eeb87a55238b731
516dc6a7b5af72bc 528d2c2e4a90b778 53f2d61a9f37158c 58353f14646dbf45
5a96c698be7ce2b4 5a9a17c007977cc6 5b764938d8d38539 5cd22d9300a398b7
5de659561eb5780a 5e0148ad29681559 60307cc1fbcf8ed8 618b165d17a703e3
641327c8e3a88d21 6472da5e56dc19cf 65423520f7487223 65dba8eb25e7f3da
66a8384ca8496cf0 67bdfd5572e403be 69f5e75a1ad01f6a 6abc7303d77a6ca1
6c51882cbf545f28 6dc68770c19a9b9a 6fb9e96d449b9989 6fc0c7d9d42aa80d
70abfc42ab121177 713c4eb91880a4cb 7201f01067e06135 7242af7a64d02055
7306e1203fd85a87 75a1e5f21a0e2a6c 76da724f003c603c 77a9ff8ce171dbea
7a5c5f347d6df158 7ab0b2e108567a1f 7e1fbe4a3484a32f 7eb8c48e1c649ba4
7fcccddbfbfd13ef 8133806e7f5b5a0c 820c42dbd1c9c618 845c593f973cc0e9
87c1fef4ee68bdf2 8828944fda492072 8a6d15326adff297 8d438e5fb6411b8e
8f8ba2ce731c8df7 91ecf12cc3e0c2ec 9220c9ff0ad98d26 94c1af954d47200b
98cf8ca565a8e3e1 9900ffa32d6779be 999237435fcfe7ff 9b2160e71f7d54ef
9b75eefc16bd94a9 9c1bcd2a7e4b8c6a 9ec42ecab97d1a4c a062bcc4c66bbc3c
a13e6e4c9e661dd6 a3672f3ba826d112 a4421c5d7f77f10e a6de3579950ccdc3
aea5cbcf63878ac5 af936bf30b153029 b134ca9a26172ddb b2610909af556e4f
b409e1b91a201ca4 b5e7cf1479784d84 b716013cafcadfb5 b8f773a2f83e878a
b93687b8711d6500 bac3c9c4f7cbca32 bb844456b5bd2445 bda3803ebbaff454
bdb7c5de2f6b0d05 be8e22efc5cdad4b be9a05b2836e50fe c027dff520952d75
c1756770dfb838ee c66d1bc8d14afe6b c75ba330f21fa993 ca15767eb23fbf3c
cb3478f16cc0c80c cba4f3eaf4dec4c0 cd01da88eb1f01df cde686555200efc9
cfae0d4248f7142f d5e33e1005c21a20 d775227347b87570 d7de3273c7a7f6b2
d81165bfc163b0d0 d8e61a42a335fc85 da15c82250769ffa dbb873f5cb654cd9
dd666a6ba2bfadf3 ddbef9012b70447c df05adb8cf85bf4a df73ef3f9c8a90f6
e0a6000f7ee9b451 e2ea95b8abe7c92a e6c9ad2427de19ba e79f68e8138a9a04
e8124ba3a75a0df6 e9022fb6222edde3 ea611fd67ade4a60 eb965c4fdc5d3c03
ebbd3e946926ed52 ef76e3c53d8faf69 f15aa141b057bf99 f1b401f1b8b264ad
f63135d2f08dab4b f78d515712ea08d9 f9fb15aa13e12b0d fc1abfeb6f1b68fc
fdf9cee8b4418c3c ffbe9fc846897ac6

0020a8a2bb20b4b0 017f380e0b6a61cd 02b32eac19a03203 02ba79867aeef1e0
050cbabf558d4ecb 0577bde917765daa 06197cb162145559 0814740aa90e1950
0d0c40c054acc31a 0d2cf31aa2284703 0f738480e8fe03b8 10b659cf0da612cc
11e2ead3eba3776a 148a0c532bc5091b 17ad5cbb7bd1d2be 202f8c2b02ea0950
2043092d13a9ddb3 23117108fd28266f 2415764f4feed561 24d8f1f04d743caa
261627aa44cbdc94 2c0c8fef481bd01f 2ef4ed987f1ae220 34035ddf87becf93
346bd9475d2e3215 36da47992ca584c8 37a5ba1b3cedbefb 39108663732ebb04
394831e63e197bb6 3b8357c256930dd9 3ca00d59542fe219 412f6dd01f91ea0f
4220244476add4aa 4238bb4a9afb8ef4 427e5cd459495369 4281fd4242594fb8
43a15aebe25e6b53 43ae6016a35552dd 441f39bcd71b64e5 44b461f0217123f5
465a7a123c8ef8cc 47cec92a60ecda34 4a268e776e126635 4ac72cb8fe7e77fe
4af1b491da09956a 4b4e33fe3bb9a350 4d2cb169321dd299 4e5aa8ea61bd59e2
4e88f3970457636f 52da1f548e5e2ea2 53121da7a98b7de9 534a3589a6211b65
53b4ca4a22bbce94 556b2201e0c13c09 57e36152dad872e3 57ff33fde74d026c
582202d4a85dd800 5e8929db0dd5bd39 5ec1bb6bf639f03e 5ec7d3d510c47edf
65e1a5e08330cdd4 683be50d548f8ece 6d10e976ac83dd79 6d47a7057fd43813
740f976c49440e89 79c65342c7fd5f80 7a99c4d34a5f93c7 7b88a5489c96f8cb
7c75346facd241a6 7e0931955dc97aa1 8098edd7d1d5601b 8314dc304ee8b2ad
83f5841141bce63b 8448413c0db31fce 844d37f9b6d81e2a 894ab90f7ee03325
8aa2324136a181ed 8ac12a784aafed24 8b4221ef9f92d4b0 8e8e9e1e89d41810
8ed8f9587a239b38 91f85c2031bd391f 941c0062a758f227 969da0ab7387952a
974c9e54215d686d 9938602ebb977f88 a2099931061daf39 a20bdfef63be101c
a241c1d56a25e7df a5df1718f282561e a87c85293a631c1e a967d25422a0e540
ab285bc12d2069ed adb2d3408986568c ae2b5aea28b97b44 ae65f8ee35b6fa59
af35c578acd79bf8 b56e4ded8d08b5d1 b84319370f594fb0 b9afb251203db520
b9c950abd03274b0 b9cde5fb8083f99e c338b0af34ddad6d c4cf0e5ce40a86ad
c7481d6fabf20f31 c7b9f353344d8db2 c9d32c92f851b258 cf7e145b2c49112f
d11cb19291756534 d3dd27f21633653f d458915855596ff5 d515ff75ef8ed805
d64261f122e3ef3b d9f29a1818b39472 db355040cbe26392 dded3781d12baa06
de6895cedcfdb45b e1611727431c4224 e231a27e7e2d93d6 e6a4d654afa3a919
e7375f9815cfd066 e7896eac1898cb23 e9cb1329e3f38abd e9f19ceb7012b0c0
ea7321a84bbc15e7 ef63ed5daa1137fd f0e9983114ddefcf f6be3e07a2788417
""".split())


def _comment_suffixes(line: str) -> Iterable[str]:
    """_norm of the text from every '#' of a rendered template line (a
    whole comment line, a description line, a commented-out key)."""
    for m in re.finditer("#", line):
        n = _norm(line[m.start():])
        if n:
            yield n


def _doc_lines(text: str) -> List[str]:
    """A description as `# ` comment lines, wrapped."""
    if not text:
        return []
    return ["# " + ln for ln in textwrap.wrap(
        text, DOC_WIDTH, break_long_words=False, break_on_hyphens=False)]


# ------------------------------------------------------------ rendering

Slot = Tuple[Any, ...]       # ("hdr", T) | ("key", T, key) | ("end", T)


@dataclass
class Run:
    """User comment lines that travel together."""
    lines: List[str]
    blank_before: bool = False
    blank_after: bool = False


@dataclass
class Extras:
    """What the user's file adds to the template: comment runs by slot,
    trailing comments and verbatim values by slot, and the notes."""
    runs: Dict[Slot, List[Run]] = field(default_factory=dict)
    trailing: Dict[Slot, str] = field(default_factory=dict)
    verbatim: Dict[Slot, str] = field(default_factory=dict)
    notes: List[Run] = field(default_factory=list)


@dataclass
class _Entry:
    """One key line of a table, to be laid out with the others."""
    key: str
    value: str                 # TOML text (may span lines)
    doc: str = ""              # the filled description
    commented: bool = False
    slot: bool = True          # a place the user's comments can attach to

    @property
    def prefix(self) -> str:
        return ("# " if self.commented else "") + key_repr(self.key)


class _Renderer:
    def __init__(self, doc: Dict[str, Any], country: str, year: int,
                 extras: Optional[Extras] = None):
        self.doc = doc
        self.country = country
        self.year = year
        self.x = extras or Extras()
        self.lines: List[str] = []
        self.template_lines: List[str] = []     # the template's own lines
        self.tables: Set[Tuple[Any, ...]] = set()
        self.slots: Set[Slot] = set()
        self.unknown: List[str] = []             # "[table] key" names
        self.subst = {
            "{year}": str(year), "{prev_year}": str(year - 1),
            "{next_year}": str(year + 1), "{country}": country,
            "{home}": C.home_currency(country),
            "{tax_date}": C.default_tax_date(country),
            "{tz}": _EXAMPLE_TZ,
            "{provinces}": _provinces() if country == C.CANADA else "",
            "{plans}": _plans(country),
            "{bases}": _bases() if country == C.CANADA else "",
        }

    # -- primitives
    def fill(self, text: str, name: str = "NAME") -> str:
        for k, v in self.subst.items():
            text = text.replace(k, v)
        text = text.replace("{name}", name)
        return text.replace("{{", "{").replace("}}", "}")

    def emit(self, line: str, *, template: bool = True) -> None:
        self.lines.append(line)
        if template:
            self.template_lines.append(line)

    def blank(self) -> None:
        if self.lines and self.lines[-1] != "":
            self.lines.append("")

    def table(self, T: Tuple[Any, ...]) -> None:
        self.tables.add(T)
        self.slots.add(("end", T))

    def user_runs(self, slot: Slot) -> None:
        self.slots.add(slot)
        runs = self.x.runs.get(slot, ())
        for i, run in enumerate(runs):
            for ln in run.lines:
                self.emit(ln, template=False)
            # (never a blank between the last run and its line)
            if run.blank_after and i < len(runs) - 1:
                self.lines.append("")

    def end(self, T: Tuple[Any, ...]) -> None:
        for run in self.x.runs.get(("end", T), ()):
            if run.blank_before:
                self.blank()
            for ln in run.lines:
                self.emit(ln, template=False)

    def header(self, T: Tuple[Any, ...], text: str, *,
               commented: bool = False, doc: str = "") -> None:
        """A table line, its heading above it; the user's comments (and a
        trailing comment, moved onto its own line) between the two."""
        slot = ("hdr", T)
        self.table(T)
        for ln in _doc_lines(doc):
            self.emit(ln)
        self.user_runs(slot)
        tr = self.x.trailing.get(slot)
        if tr and not commented:
            self.emit(tr, template=False)
        self.emit(("# " if commented else "") + text)

    def keys(self, T: Tuple[Any, ...],
             entries: Sequence[Union[_Entry, str]]) -> None:
        """A table's key lines (and its heading lines, as str), directly
        under its table line: one `=` column for the whole table, each
        key's description and the user's comments above it, a blank line
        before every key that has comment lines."""
        K = max([len(e.prefix) for e in entries if isinstance(e, _Entry)]
                or [0])
        first = True
        for e in entries:
            if isinstance(e, str):
                self.blank()
                self.emit(e)
                first = True
                continue
            s = ("key", T, e.key)
            doc = _doc_lines(e.doc)
            runs = self.x.runs.get(s, ()) if e.slot else ()
            tr = (self.x.trailing.get(s)
                  if e.slot and not e.commented else None)
            if not first and (doc or runs or tr):
                self.blank()
            first = False
            for ln in doc:
                self.emit(ln)
            if e.slot:
                self.user_runs(s)
            if tr:
                self.emit(tr, template=False)
            verbatim = (e.slot and not e.commented
                        and s in self.x.verbatim)
            value = self.x.verbatim[s] if verbatim else e.value
            vlines = value.split("\n")
            pre = "# " if e.commented else ""
            self.emit(f"{e.prefix.ljust(K)} = {vlines[0]}",
                      template=not verbatim)
            for ln in vlines[1:]:
                # A verbatim value is the user's text, never matched as
                # template text.
                self.emit(pre + ln, template=not verbatim)

    def entry(self, k: Key, values: Mapping[str, Any],
              name: str = "NAME") -> _Entry:
        """Key `k` set (active, with its value) or not (commented, with
        its default or example)."""
        c = self.country
        doc = self.fill(_pick(k.doc, c), name)
        if k.name in values:
            return _Entry(k.name, toml_value(values[k.name], top=True), doc)
        return _Entry(k.name, self.fill(_pick(k.value, c), name), doc,
                      commented=True)

    def unknown_entries(self, values: Mapping[str, Any],
                        known: Sequence[str], label: str, *,
                        owner_table: Optional[str] = None
                        ) -> List[Union[_Entry, str]]:
        c = self.country
        extra = sorted(k for k in values
                       if k not in known
                       or (owner_table and not owned(c, owner_table, k)))
        if not extra:
            return []
        for k in extra:
            self.unknown.append(f"[{label}] {k}")
        return [_UNKNOWN_KEYS_LINE] + [
            _Entry(k, toml_value(values[k], top=True)) for k in extra]

    # -- sections
    def render(self) -> str:
        c = self.country
        doc = self.doc
        for ln in _FILE_HEADER:
            self.emit(self.fill(ln))
        self.table(())
        self.unknown_top()
        self.end(())
        self.lines.append("")
        self.settings(doc.get("settings") or {})
        self.accounts(doc.get("accounts"), "accounts" in doc)
        for t in TABLES:
            if not owned(c, t.name):
                continue
            self.lines.append("")
            ok = t.name in doc and self._shape_ok(t.name, doc[t.name])
            if t.aot:
                self.aot(t, doc[t.name] if ok else None)
            else:
                self.plain_table(t, doc[t.name] if ok else None, ok)
        self.unknown_tables()
        if self.x.notes:
            self.blank()
            self.emit(NOTES_HEADING)
            self.table(("__notes__",))
            for run in self.x.notes:
                if run.blank_before:
                    self.blank()
                for ln in run.lines:
                    self.emit(ln, template=False)
        while self.lines and self.lines[-1] == "":
            self.lines.pop()
        return "\n".join(self.lines) + "\n"

    def unknown_top(self) -> None:
        keys = sorted(k for k, v in self.doc.items()
                      if not self._is_table(k, v))
        if not keys:
            return
        self.unknown.extend(keys)
        self.keys((), [_UNKNOWN_TOP_LINE] + [
            _Entry(k, toml_value(self.doc[k], top=True)) for k in keys])

    def _is_table(self, k: str, v: Any) -> bool:
        if isinstance(v, dict):
            return True
        return (isinstance(v, list) and bool(v)
                and all(isinstance(e, dict) for e in v))

    def settings(self, s: Dict[str, Any]) -> None:
        c = self.country
        T = ("settings",)
        self.header(T, "[settings]")
        keys = _sorted_keys(k for k in SETTINGS_SPEC
                            if owned(c, "settings", k.name))
        self.keys(T, [self.entry(k, s) for k in keys]
                  + self.unknown_entries(s, spec_keys()["settings"],
                                         "settings",
                                         owner_table="settings"))
        self.end(T)

    def accounts(self, accts: Any, present: bool) -> None:
        c = self.country
        self.lines.append("")
        T0 = ("accounts",)
        self.table(T0)
        for ln in _doc_lines(_ACCOUNTS_DOC):
            self.emit(ln)
        # The reference block: every account key, commented (no place
        # for the user's comments: they go to the next table).
        self.emit("# [accounts.NAME]")
        self.keys(T0, [
            _Entry(k.name, self.fill(_pick(k.value, c)),
                   self.fill(_pick(k.doc, c)), commented=True, slot=False)
            for k in _sorted_keys(ACCOUNT_SPEC)])
        if present and not accts:
            self.lines.append("")
            self.header(T0, "[accounts]")
        self.end(T0)
        names = list(accts or {})
        for name in names:
            acfg = accts[name]
            T = ("accounts", name)
            self.lines.append("")
            self.header(T, f"[accounts.{key_repr(name)}]")
            keys = _sorted_keys(k for k in ACCOUNT_SPEC if k.name in acfg)
            self.keys(T, [self.entry(k, acfg, name) for k in keys]
                      + self.unknown_entries(acfg, spec_keys()["accounts"],
                                             f"accounts.{name}"))
            self.end(T)
        more, intro = _MORE_ACCOUNT[c]
        if more not in names:
            T = ("accounts", more)
            self.lines.append("")
            self.header(T, f"[accounts.{more}]", commented=True, doc=intro)
            spec = {k.name: k for k in ACCOUNT_SPEC}
            self.keys(T, [
                _Entry(n, v, self.fill(_pick(spec[n].doc, c)),
                       commented=True)
                for n, v in (("transfers", "true"),
                             ("type", '"sheltered"'))])
            self.end(T)

    def plain_table(self, t: Table, values: Any, present: bool) -> None:
        c = self.country
        T = (t.name,)
        self.header(T, f"[{t.name}]", commented=not present,
                    doc=self.fill(_pick(t.doc, c)))
        keys = _sorted_keys(k for k in t.keys if owned(c, t.name, k.name))
        vals = values if isinstance(values, dict) else {}
        self.keys(T, [self.entry(k, vals) for k in keys]
                  + self.unknown_entries(vals, [k.name for k in keys],
                                         t.name, owner_table=t.name))
        self.end(T)

    def aot(self, t: Table, entries: Any) -> None:
        c = self.country
        T0 = (t.name,)
        self.header(T0, f"[[{t.name}]]", commented=True,
                    doc=self.fill(_pick(t.doc, c)))
        self.keys(T0, [self.entry(k, {}) for k in _sorted_keys(t.keys)])
        self.end(T0)
        for i, e in enumerate(entries or []):
            T = (t.name, i)
            self.lines.append("")
            self.header(T, f"[[{t.name}]]")
            keys = sorted(k.name for k in t.keys if k.name in e)
            self.keys(T, [_Entry(k, toml_value(e[k], top=True))
                          for k in keys]
                      + self.unknown_entries(e, [k.name for k in t.keys],
                                             f"[{t.name}] #{i + 1}"))
            self.end(T)

    def unknown_tables(self) -> None:
        c = self.country
        names = [k for k, v in self.doc.items()
                 if self._is_table(k, v) and not (
                     k in ("settings", "accounts")
                     or (k in TABLE_BY_NAME and owned(c, k)
                         and self._shape_ok(k, v)))]
        if not names:
            return
        self.lines.append("")
        self.emit(_UNKNOWN_TABLES_LINE)
        for k in names:
            self.unknown.append(f"[{k}]")
            v = self.doc[k]
            for i, e in (((None, v),) if isinstance(v, dict)
                         else enumerate(v)):
                T = (k,) if i is None else (k, i)
                self.lines.append("")
                self.header(T, f"[{key_repr(k)}]" if i is None
                            else f"[[{key_repr(k)}]]")
                self.keys(T, [_Entry(x, toml_value(e[x], top=True))
                              for x in sorted(e)])
                self.end(T)

    @staticmethod
    def _shape_ok(name: str, v: Any) -> bool:
        t = TABLE_BY_NAME[name]
        if t.aot:
            return isinstance(v, list) and all(isinstance(e, dict)
                                               for e in v)
        return isinstance(v, dict)


# ------------------------------------------------------------ entry points

def scaffold_document(country: str, year: int,
                      tz: Optional[str] = None) -> Dict[str, Any]:
    """The parsed config `taxjson init` writes for `country`."""
    country = C.canonical_country(country)
    s: Dict[str, Any] = {
        "year": year, "country": country,
        "base_currency": C.home_currency(country),
        "tax_date": C.default_tax_date(country),
    }
    if country == C.CANADA:
        # A US project holds US dollars only unless it says otherwise:
        # fetching CAD rates for an all-USD user is noise (and egress).
        s["source_currencies"] = ["USD"]
    if tz:
        s["local_timezone"] = tz
    if country == C.CANADA:
        # s.49(1) grant timing from the first year filed under it: the
        # scaffold sets it (a later project must keep the first value).
        s["option_grant_timing_since"] = year
    accounts: Dict[str, Any] = {}
    for name in SCAFFOLD_ACCOUNTS[country]:
        if name == "margin":
            accounts[name] = {"type": "taxable"}
        elif name == "crypto":
            accounts[name] = {"type": "taxable", "crypto": True}
        else:
            accounts[name] = {"type": "sheltered", "transfers": True}
    return {"settings": s, "accounts": accounts}


def render_document(doc: Dict[str, Any], country: str,
                    year: Optional[int] = None) -> str:
    """The template filled with a parsed config's values (no user
    comments)."""
    country = C.canonical_country(country)
    return _Renderer(doc, country, _year_of(doc, year)).render()


# What to download, per broker — the inputs/<account>/README.txt `taxjson
# init` writes (docs/getting-started.md step 3 says the same).
_EQUITY_EXPORTS = (
    ("Interactive Brokers", "Activity Statement, CSV: the longest period "
                            "allowed (one file per year is fine)."),
    ("Questrade", "Account activity, CSV: every year available."),
    ("RBC Direct Investing", "Transaction history, CSV: every year "
                             "available."),
    ("Webull", "Trading Summary, CSV: buys and sells only; enter "
               "dividends and interest from your slips."),
    ("Any other broker", "Any CSV, plus a column mapping (README, \"Any "
                         "other broker\")."),
)
_CRYPTO_EXPORTS = (
    ("Kraken", "Trades AND Ledgers, CSV: both, every year available."),
    ("Coinbase", "Transaction history, CSV: every year available."),
)


def input_readme(country: str, name: str) -> str:
    """The README.txt `taxjson init` writes in inputs/<name>/: which
    export to download from each broker (all the history there is, plus
    a positions report), for the scaffold account `name`."""
    country = C.canonical_country(country)
    crypto = name == "crypto"
    sheltered = name not in ("margin", "crypto")
    slips = "T5008" if country == C.CANADA else "1099-B"
    loss_rule = ("superficial-loss" if country == C.CANADA
                 else "wash-sale")
    rows = _CRYPTO_EXPORTS if crypto else _EQUITY_EXPORTS
    w = max(len(b) for b, _ in rows) + 2
    lines = [
        *textwrap.wrap(f"inputs/{name}/ holds the files of the account "
                       f"[accounts.{name}] in taxjson.toml.", 72),
        "",
        "Put this account's broker exports here. Any file name works:",
        "taxjson recognises each export by its header, and `taxjson run`",
        "prints which broker it read each file as. Several files are fine;",
        "rows that overlap are read once.",
        "",
        "Download ALL the history the broker will give you, not just the",
        "tax year: the cost of something sold this year comes from the day",
        "you bought it, which may be years back.",
        "",
    ]
    for broker, what in rows:
        wrapped = textwrap.wrap(what, 70 - w) or [""]
        lines.append(f"  {broker:<{w}}{wrapped[0]}")
        lines += [f"  {'':<{w}}{x}" for x in wrapped[1:]]
    lines += [
        "",
        "Also save a positions report with book cost (\"Holdings\",",
        "\"Positions\" or \"Portfolio\") for the date your download",
        "starts and for the end of the tax year. An Interactive Brokers",
        "Activity Statement (CSV) or an RBC Holdings Export is read as it",
        "is; from another broker, type it into a [[holding]] .toml file.",
        "The first becomes the opening balance (`taxjson opening",
        "ACCOUNT FILE` writes an opening_DATE.tt here); the second is",
        "what `taxjson sanity` checks the books against (quantities and",
        "costs). `taxjson run` skips a positions report left here.",
        "",
        "Purchases the download does not reach go in a .tt file in this",
        "folder: docs/getting-started.md, step 5.",
    ]
    if sheltered:
        lines += [
            "",
            "Even though a sheltered account owes no tax, its purchases",
            f"count for the {loss_rule} rule on your taxable accounts:",
            "include its files too.",
        ]
    elif not crypto:
        lines += [
            "",
            f"Your broker's {slips} slips are checked against the books by",
            "`taxjson reconcile-slips` (keep them in inputs/slips/).",
        ]
    lines += [
        "",
        *textwrap.wrap(f"No such account? Delete this folder and its "
                       f"[accounts.{name}] section from taxjson.toml.", 72),
    ]
    return "\n".join(lines) + "\n"


def render_init(country: str, year: Optional[int] = None,
                tz: Optional[str] = None) -> Tuple[str, Tuple[str, ...]]:
    """(taxjson.toml text, account names) for `taxjson init`."""
    country = C.canonical_country(country)
    yr = int(year) if year is not None else _date.today().year
    doc = scaffold_document(country, yr, tz)
    return render_document(doc, country, yr), tuple(doc["accounts"])


def _year_of(doc: Mapping[str, Any], year: Optional[int] = None) -> int:
    if year is not None:
        return int(year)
    y = (doc.get("settings") or {}).get("year") \
        if isinstance(doc.get("settings"), dict) else None
    if isinstance(y, int) and not isinstance(y, bool) and 1900 <= y <= 9999:
        return y
    return _date.today().year


# ------------------------------------------------------------ scanning

@dataclass
class _Item:
    kind: str                  # blank | comment | header | kv
    line: int
    text: str = ""             # the comment line / header text
    path: Tuple[Any, ...] = ()
    aot: bool = False
    trailing: Optional[str] = None
    value: str = ""
    inner: bool = False        # a multi-line value with comments inside
    inner_comments: List[str] = field(default_factory=list)
    end_line: int = 0


_KEY_PART = re.compile(r"[A-Za-z0-9_-]+")


def _parse_keypath(s: str, i: int) -> Tuple[Tuple[str, ...], int]:
    """A dotted key starting at s[i]: (parts, index after it)."""
    parts: List[str] = []
    n = len(s)
    while True:
        while i < n and s[i] in " \t":
            i += 1
        if i >= n:
            raise ValueError("key expected")
        if s[i] == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            q = s[i:j + 1]
            parts.append(next(iter(tomllib.loads(f"{q} = 0"))))
            i = j + 1
        elif s[i] == "'":
            j = s.index("'", i + 1)
            parts.append(s[i + 1:j])
            i = j + 1
        else:
            m = _KEY_PART.match(s, i)
            if not m:
                raise ValueError("key expected")
            parts.append(m.group(0))
            i = m.end()
        while i < n and s[i] in " \t":
            i += 1
        if i < n and s[i] == ".":
            i += 1
            continue
        return tuple(parts), i


def _parse_header(s: str) -> Tuple[Tuple[str, ...], bool, str]:
    """`[a.b]` / `[[a]]` text (no leading blanks) -> (path, aot, rest
    after the closing bracket)."""
    aot = s.startswith("[[")
    i = 2 if aot else 1
    path, i = _parse_keypath(s, i)
    close = "]]" if aot else "]"
    if not s.startswith(close, i):
        raise ValueError("header")
    return path, aot, s[i + len(close):]


def _scan(text: str) -> List[_Item]:
    """The file as items, for a text tomllib has already accepted."""
    items: List[_Item] = []
    lines = text.split("\n")
    # Line start offsets, for the value lexer.
    starts = [0]
    for ln in lines[:-1]:
        starts.append(starts[-1] + len(ln) + 1)
    n = len(text)

    def line_of(pos: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    li = 0
    while li < len(lines):
        raw = lines[li]
        st = raw.strip()
        if not st:
            items.append(_Item("blank", li))
            li += 1
            continue
        if st.startswith("#"):
            items.append(_Item("comment", li, text=st))
            li += 1
            continue
        if st.startswith("["):
            path, aot, rest = _parse_header(st)
            rest = rest.strip()
            items.append(_Item("header", li, text=st, path=path, aot=aot,
                               trailing=rest if rest.startswith("#")
                               else None))
            li += 1
            continue
        # key = value
        off = starts[li] + (len(raw) - len(raw.lstrip()))
        kp, i = _parse_keypath(text, off)
        assert text[i] == "="
        i += 1
        vstart = i
        depth = 0
        comments: List[Tuple[int, int]] = []    # (start, end) offsets
        p = i
        while p < n:
            ch = text[p]
            if text.startswith('"""', p):
                q = p + 3
                while True:
                    k = text.index('"""', q)
                    bs = 0
                    while text[k - 1 - bs] == "\\":
                        bs += 1
                    if bs % 2:
                        q = k + 1
                        continue
                    while text.startswith('"', k + 3):
                        k += 1
                    p = k + 3
                    break
                continue
            if text.startswith("'''", p):
                k = text.index("'''", p + 3)
                while text.startswith("'", k + 3):
                    k += 1
                p = k + 3
                continue
            if ch == '"':
                q = p + 1
                while text[q] != '"':
                    q += 2 if text[q] == "\\" else 1
                p = q + 1
                continue
            if ch == "'":
                p = text.index("'", p + 1) + 1
                continue
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "#":
                e = text.find("\n", p)
                e = n if e < 0 else e
                comments.append((p, e))
                p = e
                continue
            elif ch == "\n" and depth <= 0:
                break
            p += 1
        end = p
        last_line = line_of(end - 1 if end > vstart else vstart)
        trailing = None
        inner: List[str] = []
        vend = end
        for cs, ce in comments:
            if line_of(cs) == last_line and ce == end:
                trailing = text[cs:ce].rstrip()
                vend = cs
            else:
                inner.append(text[cs:ce].rstrip())
        value = text[vstart:vend].strip()
        items.append(_Item("kv", li, path=kp, trailing=trailing,
                           value=value, inner=bool(inner),
                           inner_comments=inner, end_line=last_line))
        li = last_line + 1
    return items


_COMMENTED_KEY = re.compile(r"#\s?([A-Za-z0-9_-]+)\s*=")


def _commented_header(st: str) -> Optional[Tuple[Tuple[str, ...], bool]]:
    body = st.lstrip("#").strip()
    if not body.startswith("["):
        return None
    try:
        path, aot, rest = _parse_header(body)
    except (ValueError, KeyError, IndexError, tomllib.TOMLDecodeError):
        return None
    rest = rest.strip()
    if rest and not rest.startswith("#"):
        return None
    return path, aot


@dataclass
class FormatResult:
    text: str                     # the formatted file
    changed: bool
    unrecognised: List[str]       # "[table] key" / "[table]" / "key"
    notes_lines: int              # comment lines in the notes block
    kept_comments: int            # user comment lines kept (all places)


def format_config(text: str) -> FormatResult:
    """`text` (a taxjson.toml) re-rendered into the country's template.
    Raises FormatError when it is not valid TOML, has no usable country,
    or cannot be formatted losslessly (the parsed result must equal the
    input's — checked here, before anything is returned)."""
    if tomllib is None:  # pragma: no cover
        raise FormatError("TOML support requires Python 3.11+ or tomli")
    if text.startswith("﻿"):
        text = text[1:]
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise FormatError(f"taxjson.toml is not valid TOML: {e}") from None
    settings = doc.get("settings")
    if not isinstance(settings, dict):
        raise FormatError("taxjson.toml has no [settings] table — "
                          "`taxjson format` needs [settings] country")
    try:
        country = C.settings_country(settings)
    except C.CountryError as e:
        raise FormatError(str(e)) from None
    accts = doc.get("accounts")
    if accts is not None and not (
            isinstance(accts, dict)
            and all(isinstance(v, dict) for v in accts.values())):
        raise FormatError("[accounts] must hold only [accounts.NAME] "
                          "tables — fix it by hand, then format")
    year = _year_of(doc)
    probe = _Renderer(doc, country, year)
    probe.render()
    template = set()
    for ln in probe.template_lines:
        template.update(_comment_suffixes(ln))
    # The all-commented scaffold too: a section the user removed or
    # filled in still had the template's lines.
    for d in ({"settings": {"country": country}},
              scaffold_document(country, year)):
        r = _Renderer(d, country, year)
        r.render()
        for ln in r.template_lines:
            template.update(_comment_suffixes(ln))
    template.add(_norm(NOTES_HEADING))

    def is_template(comment: str) -> bool:
        n = _norm(comment)
        return not n or n in template or _hash(n) in _LEGACY_TEMPLATE_HASHES

    extras, kept = _associate(_scan(text), probe, is_template)
    r = _Renderer(doc, country, year, extras)
    out = r.render()
    # Lossless, or nothing: the effective config must not change.
    try:
        after = tomllib.loads(out)
    except tomllib.TOMLDecodeError as e:  # pragma: no cover
        raise FormatError(f"internal error: the formatted file is not "
                          f"valid TOML ({e}); nothing was written")
    if after != doc or list(after.get("accounts") or {}) != list(accts or {}):
        raise FormatError("internal error: the formatted file would not "
                          "load to the same configuration; nothing was "
                          "written")
    out_lines = out.split("\n")
    missing = _missing_lines(kept, out_lines)
    if missing:
        raise FormatError(f"internal error: {missing} of your comment "
                          f"line(s) would be lost; nothing was written")
    notes = sum(len(run.lines) for run in extras.notes)
    return FormatResult(text=out, changed=(out != text),
                        unrecognised=r.unknown, notes_lines=notes,
                        kept_comments=len(kept))


def _missing_lines(kept: List[str], out_lines: List[str]) -> int:
    from collections import Counter
    have = Counter(ln.strip() for ln in out_lines)
    for ln in out_lines:
        # A kept trailing comment sits after a value.
        i = ln.find("#")
        while i >= 0:
            have[ln[i:].strip()] += 1
            i = ln.find("#", i + 1)
    need = Counter(k.strip() for k in kept)
    return sum(max(0, c - have[k]) for k, c in need.items())


def _associate(items: List[_Item], probe: _Renderer, is_template
               ) -> Tuple[Extras, List[str]]:
    """Attach the user's comments to the template slots they belong to
    (see the module doc)."""
    x = Extras()
    kept: List[str] = []
    tables = probe.tables
    slots = probe.slots

    def resolve(P: Tuple[Any, ...]) -> Optional[Slot]:
        for k in range(len(P), -1, -1):
            T = P[:k]
            if T in tables:
                if k == len(P):
                    return ("hdr", T) if ("hdr", T) in slots else None
                s = ("key", T, P[k])
                return s if s in slots else None
        return None

    def ctx_table(P: Optional[Tuple[Any, ...]]) -> Optional[Tuple[Any, ...]]:
        if P is None:
            return None
        for k in range(len(P), -1, -1):
            if P[:k] in tables:
                return P[:k]
        return None

    pending: List[Run] = []
    gap = False             # a blank since the last line that counted
    blank_above = False     # the line just above is blank
    active: Tuple[Any, ...] = ()
    commented_ctx: Optional[Tuple[Any, ...]] = None
    aot_count: Dict[Tuple[Any, ...], int] = {}
    in_notes = False
    # A run holding the user's own commented-out key (`# province =
    # "BC"`) goes to that key when it ends, with the runs before it.
    target: Optional[Slot] = None

    def add_user(line: str) -> None:
        nonlocal gap
        kept.append(line)
        if pending and not gap:
            pending[-1].lines.append(line)
        else:
            if pending and gap:
                pending[-1].blank_after = True
            pending.append(Run([line], blank_before=blank_above))
        gap = False

    def flush(slot: Slot) -> None:
        nonlocal gap
        if pending:
            if gap:
                pending[-1].blank_after = True
            x.runs.setdefault(slot, []).extend(pending)
            pending.clear()
        gap = False

    def set_trailing(slot: Slot, text: str) -> None:
        kept.append(text)
        if slot in x.trailing:
            # Two lines fold into one (a sub-table and its key): the
            # second comment goes above the line.
            x.runs.setdefault(slot, []).append(Run([text]))
        else:
            x.trailing[slot] = text

    def before_header() -> None:
        """At a table line: the runs a blank separates from it belong to
        the end of the table they were in; a run directly above it stays
        with it (flushed by the caller)."""
        if not pending:
            return
        if gap:
            flush_end()
            return
        last = pending.pop()
        flush_end()
        pending.append(last)

    def flush_target(blank_after: bool = False) -> None:
        """The run that held a commented-out key of the user's ended: it
        (with the runs pending before it) goes above that key."""
        nonlocal target
        if target is None:
            return
        if blank_after and pending:
            pending[-1].blank_after = True
        x.runs.setdefault(target, []).extend(pending)
        pending.clear()
        target = None

    def flush_end() -> None:
        """Runs separated by a blank from the next table: the end of the
        table they were in."""
        nonlocal gap
        if not pending:
            return
        T = ctx_table(commented_ctx) if commented_ctx is not None else None
        if T is None:
            T = ctx_table(active)
        if in_notes or T is None:
            x.notes.extend(pending)
        else:
            for run in pending:
                run.blank_after = False
            x.runs.setdefault(("end", T), []).extend(pending)
        pending.clear()

    prev_blank = False
    for it in items:
        blank_above, prev_blank = prev_blank, it.kind == "blank"
        if it.kind == "blank":
            flush_target(blank_after=True)
            gap = True
            # (A commented-out section lasts to the next table line,
            # blank lines included: its keys are blank-separated.)
            continue
        if it.kind == "comment":
            st = it.text
            if not in_notes and not is_template(st):
                # The user's own line, whatever it looks like (a
                # commented-out table too): it travels with the next
                # line that has a place in the template — or, when it is
                # a commented-out key of this table, with that key.
                add_user(st)
                m = _COMMENTED_KEY.match(st)
                if m and target is None:
                    ctx = (commented_ctx if commented_ctx is not None
                           else active)
                    slot = resolve(ctx + (m.group(1),))
                    if slot is not None and slot[0] == "key":
                        target = slot
                continue
            hdr = _commented_header(st)
            if hdr is not None and target is not None:
                slot = resolve(hdr[0])
                if slot is not None and slot[0] == "hdr":
                    # A block directly above a table line stays with it.
                    target = None
            flush_target()
            if st.strip() == NOTES_HEADING.strip():
                flush_end()
                in_notes = True
                gap = False
                continue
            if in_notes:
                if not is_template(st):
                    add_user(st)
                continue
            # The template's own line: regenerated, not kept — but a
            # commented-out table or key marks the place the comments
            # above it belong to.
            if hdr is not None:
                path, _aot = hdr
                slot = resolve(path)
                if slot is not None and slot[0] == "hdr":
                    before_header()
                    flush(slot)
                    commented_ctx = path
                # (else the `# [accounts.NAME]` reference block: no
                # place of its own; the table before it goes on)
                continue
            m = _COMMENTED_KEY.match(st)
            if m:
                ctx = commented_ctx if commented_ctx is not None else active
                slot = resolve(ctx + (m.group(1),))
                if slot is not None and slot[0] == "key":
                    flush(slot)
            continue
        if it.kind == "header":
            # A block directly above a table line stays with it.
            target = None
        flush_target()
        if it.kind == "header":
            in_notes = False
            path = it.path
            if it.aot:
                n = aot_count.get(path, 0)
                aot_count[path] = n + 1
                path = path + (n,)
            slot = resolve(path)
            if slot is not None:
                # (the end of a commented-out section, when one is open)
                before_header()
                flush(slot)
            commented_ctx = None
            # else: a table written elsewhere (`[accounts]` above its
            # accounts): its comments go with the next line that is.
            active = path
            if it.trailing and not is_template(it.trailing):
                if slot is None:
                    add_user(it.trailing)
                else:
                    set_trailing(slot, it.trailing)
            continue
        # kv
        commented_ctx = None
        in_notes = False
        P = active + it.path
        slot = resolve(P)
        if slot is None or slot[0] != "key":  # pragma: no cover
            raise FormatError(f"internal error: line {it.line + 1} has no "
                              f"place in the template; nothing was "
                              f"written")
        flush(slot)
        exact = len(it.path) == 1 and slot == ("key", active, it.path[0])
        if it.inner and exact and slot not in x.verbatim:
            # Comments inside a multi-line value: the value is kept as
            # written.
            x.verbatim[slot] = it.value
        elif it.inner:
            run = Run([c for c in it.inner_comments])
            kept.extend(run.lines)
            x.runs.setdefault(slot, []).append(run)
        if it.trailing and not is_template(it.trailing):
            set_trailing(slot, it.trailing)
    flush_target()
    flush_end()
    return x, kept
