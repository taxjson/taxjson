"""The taxjson.toml template: every key taxjson reads, documented, per country.

`taxjson init` writes it with the scaffold's values; `taxjson format`
re-renders an existing project's file into it with the user's values and
comments. One renderer serves both, so a fresh scaffold is already
formatted (formatting it is a no-op).

Layout. The tables come in a fixed order: [settings]; the accounts (a
commented `# [accounts.NAME]` reference block naming every account key,
then each [accounts.NAME] table in the user's order, then a note on
adding one more); then [estimate], [carryover],
[[distributions]], and for Canada [[capital_gains_dividends]] and
[instalments]. Arrays of tables keep their entries' order. [settings]
comes in groups (SETTINGS_GROUPS: Project, Currencies, Options, Income,
Futures, Transfers, Corporate actions), each under a `## --- Name ---`
heading line, a blank line between groups, the keys alphabetical within
a group; every other table's keys
are alphabetical, except that an account table's `type` (the required
key) comes first. Active and commented-out keys are interleaved in one
sequence, and every key line of a table (`key` or `# key`) is padded to
the table's widest so the `=` signs form one column. A key's description
is on the line(s) just above it (`## text`, wrapped), with no blank
line between keys. Comment lines that are prose (the file header,
headings, descriptions, notes) start with `## ` (PROSE); a commented-out
key or table, and each line of a commented-out multi-line value, with a
single `# `, so deleting the `# ` switches it on. The one end-of-line
comment is a key's `inline` text (the values it takes, `# settle |
trade`, or what `true` means for a switch; a single `#`: after a value
it cannot be mistaken for a commented-out line), at one column per group
or table (at most INLINE_MAX_COLUMN; a longer or multi-line value gets
it on the line above instead). An
[accounts.NAME] table is compact: key lines only, since the reference
block documents every account key once. A key that is set is written
active with its value; a key that is not set is written commented out,
`# key = default` (or an example where it has none). A table that is
absent is written commented out. Only the keys the project's country owns are listed
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
- comments, each of the user's comment lines written in the file's
  convention (comment_line): one whose text is TOML — a key and value
  (known or not), a table line, a line of a commented-out multi-line
  value — as `# text`, any other as `## text` (the leading '#'s and one
  blank replaced, the text kept; a commented-out multi-line value that
  holds a line of the user's is kept whole). A trailing comment on a
  key or table line moves to its own line just above that line (after
  the key's description; written in the convention too), with the
  comment lines directly under it that continue it (indented past the
  line's start, padded with a second `#`, or padded out to its column —
  the old aligned layout's wrapping; `_continuation`), padding dropped;
  a comment block stays directly above the key or table line that
  follows it (a commented-out key counts: the block goes above that
  key's template line), and so moves with that key to its alphabetical
  place; a block that holds a commented-out key of your own (`# province
  = "BC"`) goes to that key; a block separated by a blank line from the next table goes
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

# Comment lines that are prose (the file header, headings, descriptions,
# notes) start with PROSE, `## `; a commented-out key or table (what a
# user switches on by deleting the `# `) with a single `# `. An
# end-of-line comment after a value keeps a single `#`: it follows a
# value, so it cannot be mistaken for a commented-out line.
PROSE = "## "

# Descriptions are wrapped to this many characters after the PROSE mark
# (the line is then at most LINE_WIDTH = 100 long, the house width).
DOC_WIDTH = 97
# An inline (end-of-line) comment starts at most at this column, and its
# line is at most LINE_WIDTH long; a key whose value is longer gets it on
# the line above instead.
INLINE_MAX_COLUMN = 50
LINE_WIDTH = 100

NOTES_HEADING = ("## Your notes (kept by tjs format) — comments it could "
                 "not attach to a setting:")
_UNKNOWN_KEYS_LINE = ("## Not in the template (kept by tjs format; taxjson "
                      "does not read these — check the spelling):")
_UNKNOWN_TOP_LINE = ("## Not in the template (kept by tjs format): "
                     "top-level keys taxjson does not read.")
_UNKNOWN_TABLES_LINE = ("## Not in the template (kept by tjs format): "
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
    str, or {country: str}), written wrapped on the lines above it;
    `inline` is a short text written after the value as an end-of-line
    comment — the values the key takes (`settle | trade`), or what
    `true` means for a switch — aligned with the other inline comments
    of its group or table."""
    name: str
    value: Text
    doc: Text
    inline: Text = ""


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
    "## taxjson configuration — https://github.com/taxjson/taxjson",
    "##",
    "## Every key taxjson reads is listed here: [settings] in groups, the other tables' keys",
    "## alphabetically (an account's `type` first), one `=` column per table, so year-over-year projects",
    "## diff cleanly:",
    "##   diff ~/taxes/{prev_year}/taxjson.toml ~/taxes/{year}/taxjson.toml",
    "## Each key's description is on the `## ` lines above it, the values it takes after it. A",
    "## commented-out key (`# key = value`) shows its default (or an example where it has none): delete",
    "## the `# ` to change it. `taxjson format` puts an edited file back into this layout, keeping your",
    "## values and comments.",
)

# [settings]. A key the project's country does not own is left out
# (lib/country.SETTING_COUNTRY). Written by SETTINGS_GROUPS: group by
# group, alphabetically within a group.
SETTINGS_SPEC: Tuple[Key, ...] = (
    Key("year", "{year}",
        "The tax year the pipeline reports on (required)."),
    Key("country", '"{country}"', "",
        inline="canada | ca | usa | us (required)"),
    Key("province", '"ON"',
        "The province `taxjson estimate` taxes at. No default.",
        inline="{provinces}"),
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
        {"canada": "Default settle: CRA dates a sale by settlement.",
         "usa": "Default trade: the IRS dates a sale by trade date."},
        inline={"canada": "settle | trade", "usa": "trade | settle"}),
    Key("local_timezone", '"{tz}"',
        "The zone crypto UTC times are dated in (an IANA name). No "
        "default: required with a crypto account."),
    Key("prior_year_record", '"../{prev_year}/filed/{prev_year}.json"',
        "Last year's close-year record (`taxjson handoff`). No default."),
    Key("inputs_dir", '"../inputs"',
        "The folder of the broker exports (inputs/<account>/), when the "
        "years share one beside the year folders. Default: inputs/ here."),
    Key("holdings_dir", '"holdings"',
        "The year's broker positions snapshots `taxjson sanity` reads. "
        "Default: holdings/ here."),
    Key("exports_dir", '"../exports"',
        "Where the newest year's run copies its positions and wash radar "
        "for other tools. No default."),
    Key("tobase_map", '"../tobase.map"',
        "The tobase.map every year reads (the interlisted pairs), beside "
        "the year folders. Default: tobase.map here."),
    Key("leaps_months", "9",
        "Options bought more than this many months before expiry count as "
        "LEAPS (the leaps and leaps-sum views only; no tax figure). "
        "Default 9."),
    Key("futures_settle", '"trade"',
        "trade (the default): futures and futures options settle on the "
        "TRADE date (daily variation margin); next_day: the clearing "
        "premium date.",
        inline="trade | next_day"),
    Key("fx_cash_gains", "false",
        {"canada": "true: an end-of-run FX-on-cash report (s.39(1.1), $200 "
                   "de minimis).",
         "usa": "true: an end-of-run FX-on-cash report (§988, ordinary "
                "income)."}),
    Key("fx_cash_ledger", '"v1"',
        "The FX-on-cash ledger: v1 (the default) reads only trades and "
        "income and is NOT RELIABLE — never a filing figure; v2 (opt-in, "
        "under audit) also reads conversions, deposits/withdrawals and "
        "statement balances, models margin debt and refuses instead of "
        "guessing.",
        inline="v1 | v2"),
    Key("fx_cash_inflow_cost", '"declared"',
        "Ledger v2: money from outside the books needs a declared cost "
        "(a .tt CASHMOVE line); \"spot\" takes the day's rate for every "
        "undeclared inflow instead.",
        inline="declared | spot"),
    Key("foreign_return_of_capital", '"dividend"',
        "A non-Canadian issuer's return of capital (IB): a dividend "
        "(s.90(1), the default), or \"acb\" to lower the shares' ACB.",
        inline="dividend | acb"),
    Key("corporate_distributions", '["XYZQ.TO"]',
        "Canadian issuers whose distributions are a corporation's, dated "
        "when paid (docs/tax-rules.md, \"Income dating\"). No default."),
    Key("ric_january_dividends", '["XYZQ.US {next_year}-01-30"]',
        "January fund/REIT dividends taxed as received Dec 31 (IRC "
        "§852(b)(7) / §857(b)(9)): \"SYMBOL\" (every January one) or "
        "\"SYMBOL YYYY-01-DD\" (that payment). No default."),
    Key("option_premium_timing", '"grant"',
        "\"grant\" (the default): a written option's premium is a gain in "
        "the year WRITTEN (ITA s.49(1)); \"close\": it is netted at the "
        "closing transaction instead. See `taxjson option-boundary`.",
        inline="grant | close"),
    Key("option_grant_timing_since", "{year}",
        "Only if you write (sell to open) options: the first tax year you "
        "file their premium in the year written. Set it once and keep it "
        "in every later year (default: `year`)."),
    Key("option_buyback_loss_superficial", "false",
        "true: the strict s.54 reading — a loss on buying back a written "
        "option is superficial when identical options are bought within "
        "30 days and still held."),
    Key("transfers_as_acquisitions", "false",
        {"canada": "false (the default): a sheltered account's transfer "
                   "in or out is a move between accounts — its shares "
                   "count as held, but it is never a purchase for the "
                   "superficial-loss rule; the run warns once about each "
                   "transfer-in near a loss. true: every such transfer "
                   "is a purchase or sale on its date (an arrival date "
                   "in a loss's window then stops the run).",
         "usa": "false (the default): a retirement account's transfer "
                "in or out is a move between accounts, never a "
                "replacement purchase for the wash-sale rule; the run "
                "warns once about each transfer-in near a loss. true: "
                "every such transfer is a purchase or sale on its date "
                "(an arrival date in a loss's window then stops the "
                "run)."}),
    Key("sheltered_elections", '"zero"',
        "A spin-off or merger in a sheltered account with no saved "
        "election: \"zero\" (the default) books it without asking (a "
        "spin-off's new shares at $0 cost, a merger's new shares at the "
        "old shares' cost; no tax depends on it); \"ask\": asked like a "
        "taxable account's. `taxjson elect` wins either way.",
        inline="zero | ask"),
)

ACCOUNT_SPEC: Tuple[Key, ...] = (
    Key("type", '"taxable"', "", inline="taxable | sheltered (required)"),
    Key("plan", {"canada": '"rrsp"', "usa": '"ira"'},
        "The plan when the name doesn't say (default: from the name): "
        "{plans}."),
    Key("crypto", "false", "", inline="true: a Coinbase / Kraken account"),
    Key("transfers", "false", "",
        inline="true: keep TRANSFER rows (contributions/withdrawals)"),
    Key("holdings", '["~/broker/{name}_holdings.toml"]',
        "Positions files `taxjson sanity` reconciles against. Default: "
        "none."),
    Key("broker_accounts", '["ACCOUNT_ID"]',
        "The broker account ids of this account's statements: a holdings/ "
        "snapshot whose [meta] account is one of them is this account's. "
        "Default: `account`."),
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
    Key("brokerage", '"SOURCE"',
        "The `taxjson fetch` source of this account (a fetcher plugin's "
        "name; `taxjson fetch --list` names the installed ones). No "
        "default."),
    Key("account", '"ACCOUNT_ID"',
        "The broker account id `taxjson fetch` downloads (if its source "
        "needs one)."),
    Key("query_id", '"QUERY_ID"',
        "The report (query) id `taxjson fetch` runs (if its source needs "
        "one)."),
)

# The [settings] groups, in order: (heading, keys). Every SETTINGS_SPEC
# key is in exactly one group (tests/test_config_template.py); a group
# with no key the project's country owns is left out.
SETTINGS_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Project", ("year", "country", "province", "tax_date",
                 "local_timezone", "prior_year_record")),
    ("Folders", ("inputs_dir", "holdings_dir", "exports_dir",
                 "tobase_map")),
    ("Currencies", ("base_currency", "source_currencies", "fx_cash_gains",
                    "fx_cash_ledger", "fx_cash_inflow_cost")),
    ("Options", ("option_premium_timing", "option_grant_timing_since",
                 "option_buyback_loss_superficial", "leaps_months")),
    ("Income", ("corporate_distributions", "foreign_return_of_capital",
                "ric_january_dividends")),
    ("Futures", ("futures_settle",)),
    ("Transfers", ("transfers_as_acquisitions",)),
    ("Corporate actions", ("sheltered_elections",)),
)


def group_heading(name: str) -> str:
    """The comment line above a [settings] group."""
    return f"{PROSE}--- {name} ---"


def _account_order(keys: Iterable[Key]) -> List[Key]:
    """An account table's keys: `type` (the required key) first, the rest
    alphabetically."""
    return sorted(keys, key=lambda k: (k.name != "type", k.name))

_ACCOUNTS_DOC = (
    "One [accounts.NAME] table per folder under inputs/ (the folder name is "
    "the account name). Every key, with its default:")

# The scaffold's accounts (anything else is one more table plus folder).
SCAFFOLD_ACCOUNTS = {
    "canada": ("margin", "tfsa", "rrsp", "crypto"),
    "usa": ("margin", "roth", "401k", "crypto"),
}
_MORE_ACCOUNTS_NOTE = ("More accounts: add an [accounts.NAME] table (keys above) "
                       "and an inputs/NAME folder for it.")


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
              Key("amount", '"all"', "",
                  inline="\"all\" or the box 18 amount"),
              Key("account", '"margin"', "Default: the taxable accounts."),
          )),
    Table("instalments", False,
          "Tax instalments (`taxjson instalments`, and a summary inside "
          "`taxjson estimate`). Uncomment and fill in YOUR figures.", (
              Key("basis", '"current_year"', "", inline="{bases}"),
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
# both countries). Blocks: the file header's lines before the `## ` prose
# mark (_norm drops the leading '#'s, so every other single-'#' line of
# that layout matches the current template's `## ` text as it is);
# earlier layouts'; the grouped, end-of-line-comment layout before the
# alphabetical one. `taxjson format` regenerates them as template text
# instead of keeping them as the user's notes. Hashes, not text: the old
# examples are not carried in the source.
_LEGACY_TEMPLATE_HASHES = frozenset("""
499e05694c9cbf88 3c4a9d5a9343f70d 8c9c495fe21b9448 f39a876969ce501c

00155ff5fe04a837 b466672d86933a32 cc383b0ee44d380c ccf1f0fb14c5c312
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

# The alphabetical layout (before the grouped [settings] and compact
# tables), per country: a line only the OTHER country's template wrote
# (a Canadian `# tax_date = "trade"`) is the user's own.
_LEGACY_TEMPLATE_HASHES_BY_COUNTRY = {
    C.CANADA: frozenset("""
018a0bafa8c7de51 18a637d503929bc1 29205fde60149a2a 2a5d7d4cf458bfa6
2b8de275534aef75 45a10d8ab7062a5b 524adf3f7c0843a6 688c30e0cd5d8678
696be5069582f583 76cb5e29b150d512 a8ade4a26f32ebf6 c9bcd72c68a5c32c
daa15a61f5c9f169 eb5a968903240ffc fe1171a0a60225fa
008fbf0b21d4331d 027a359640c237cb 02fba5aeba703bc1 03b4cf3233f53254
055ae0c64ce8dc17 0953110efdc50d00 0a4d4b3ad56b1057 10ffbd73d54fbb8d
11e39a893bca603b 13b4cf05d84f74da 144d083f264e4c62 151b60b311d50294
16a41caf03a40fda 180221ee36daec4e 1b89f76d9519498f 229e72be729ea2fd
229feb6263f7d173 24aaef92b99f027c 24f367bd423c0698 2ad2402def2d23b3
2dedfc6a84a3b236 327edbc1bc62524b 3661d51afca787e9 38f7bd7c6ed94f43
399d640743def7bf 40439c64c252116c 40f92d9d5a21d406 4120da63ba553581
4246491ffcf3fae3 47d31bb3dfc98347 49741e455d2d2c28 4cdcedd54e3ed0af
4e8f42a837f0dad5 4f19f02f4cc2616a 4fd4984c8ccd4a6f 5042783462a09751
50903059cad803c4 518fd285a2db8bcc 531a08f14883fe9c 549c5be4cb70752e
55d2de201b897ccf 5e1f42eaf2188af1 611fe29fe4560408 613c63fd2316332f
62ad3579483f040d 62cba9e7c72a5113 64a583cd5f9198c9 650a42ed5fb149db
671de16c7cac75e4 671f5e4159da27e6 688d2ca159680ad0 6b426a6f926e51d4
6bec38e0b5ed819b 6e6aa315db4bc5b3 7346b294aa20c3eb 762c007b97331b7b
7715b9f75eb103fe 7992ebbf279bb8d6 7cc02743089d0ade 7d79440ff12d7185
7dd566573c3a16be 7f106d303f9d8343 80c3faca27fbad62 85c03b24ced6f059
86323e248d1089d7 87c1006f40f8eca6 8afad41408102b95 8f8b0f8beab7c729
91bb9670206b9d92 9e852a4fbd51c971 9e8d480fd17d4dd0 9f9f392221f20f4e
a0d89073041730fb a19cf1eb021fd452 a3861ce8929d447d a4bba00f6cfde9cb
a63590d20bc40879 a6f00079587f901f aa21d392980c00ea aae708cbb686e09d
ac24ac3c7d5a0a74 af49d0cafd76debc b0b6396ffabc6b73 b718a5c07b8231e5
b985bc2bd832b95e b9e29972ce74fee1 b9f0c3ce9ee2d598 ba55bdd7ba6d9d44
bf7a93e6c126b38d c0714b5d88cbbcfd c08149f1fd7f2959 c41b4c33e231bcba
c5ad5dc490b93bb2 c68116e5dc81eee2 cbc302310689ce03 ccb2c4bac9128001
cd7b647ee560a688 cf7aa29d72d3d49c cf9008e594aa56c0 d279f7b88ed86b9a
d3deda125828f747 d502d3b2f9c8ac97 d59a8a299666022e d5a36e982568dd51
d7b2f15162b61568 d8bff68fec3b6547 de12116a8b3da409 de522f2ba8ab9a17
e223b78e6cf2233d e3076f44b482d1da e4d2072628822dc0 e8f929fea88da024
e936cc2d9262deee e9380947889ee8a6 edb2bb9c2ac0f265 edffc5733f7c7f65
f23db548a616307f f38d3a0d89440041 f3c1344f0c0ee752 f4ecea16fb3371c2
f61eb9e3a92dc3ff f9e5646a07e6763c fc47e25e6fd818f4
""".split()),
    C.USA: frozenset("""
2a5d7d4cf458bfa6 2b8de275534aef75 524adf3f7c0843a6 688c30e0cd5d8678
696be5069582f583 6d10e976ac83dd79 76cb5e29b150d512 a241c1d56a25e7df
c9bcd72c68a5c32c daa15a61f5c9f169 eb5a968903240ffc fe1171a0a60225fa
008fbf0b21d4331d 027a359640c237cb 02fba5aeba703bc1 03b4cf3233f53254
0953110efdc50d00 0d372c5493fdb858 13b4cf05d84f74da 144d083f264e4c62
18781edfcb7b641b 1b89f76d9519498f 229feb6263f7d173 24bf06ace63daccc
3661d51afca787e9 399d640743def7bf 3d346a6caf411a4d 40f92d9d5a21d406
4246491ffcf3fae3 47d31bb3dfc98347 4c0e5124c885321b 5042783462a09751
549c5be4cb70752e 55980d2a6b1bbd1d 5b1c397d4f727528 5e1f42eaf2188af1
611fe29fe4560408 613c63fd2316332f 619c4fc634b14360 62ad3579483f040d
64a583cd5f9198c9 650a42ed5fb149db 671f5e4159da27e6 688d2ca159680ad0
6bec38e0b5ed819b 72c5df3267f7835b 7346b294aa20c3eb 762c007b97331b7b
7876cc1f0eb9cef6 7cc02743089d0ade 7d79440ff12d7185 83373c248476f3a2
85c03b24ced6f059 86323e248d1089d7 875c7ff422c231aa 87a3823da36c635b
87c1006f40f8eca6 89ed8d00756da518 8ce2f27ad5fb9e7c 8f8b0f8beab7c729
91bb9670206b9d92 9e852a4fbd51c971 a19cf1eb021fd452 a604b39ad8cc964f
a8947039804c67d9 aae708cbb686e09d af49d0cafd76debc af5e2bc44b589085
b0b6396ffabc6b73 b9e29972ce74fee1 b9f0c3ce9ee2d598 bf7a93e6c126b38d
c08149f1fd7f2959 c41b4c33e231bcba c5ad5dc490b93bb2 ccb2c4bac9128001
cf9008e594aa56c0 d279f7b88ed86b9a d5a36e982568dd51 d8bff68fec3b6547
e223b78e6cf2233d e4d2072628822dc0 e60e7157dfc61d39 e8f929fea88da024
e936cc2d9262deee edffc5733f7c7f65 eef93cc2bc576ad7 f1b02b5902a71e9e
f23db548a616307f f9e5646a07e6763c fc47e25e6fd818f4
""".split()),
}


def _comment_suffixes(line: str) -> Iterable[str]:
    """_norm of the text from every '#' of a rendered template line (a
    whole comment line, a description line, a commented-out key)."""
    for m in re.finditer("#", line):
        n = _norm(line[m.start():])
        if n:
            yield n


def _doc_lines(text: str) -> List[str]:
    """A description as `## ` comment lines, wrapped."""
    if not text:
        return []
    return [PROSE + ln for ln in textwrap.wrap(
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
    trailing: Dict[Slot, List[str]] = field(default_factory=dict)
    verbatim: Dict[Slot, str] = field(default_factory=dict)
    notes: List[Run] = field(default_factory=list)


@dataclass
class _Entry:
    """One key line of a table, to be laid out with the others."""
    key: str
    value: str                 # TOML text (may span lines)
    doc: str = ""              # the filled description
    commented: bool = False
    inline: str = ""           # the filled end-of-line text
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
        if not commented:
            for ln in self.x.trailing.get(slot, ()):
                self.emit(ln, template=False)
        self.emit(("# " if commented else "") + text)

    def keys(self, T: Tuple[Any, ...],
             entries: Sequence[Union[_Entry, str]]) -> None:
        """A table's key lines (and its heading lines, as str), directly
        under its table line: one `=` column for the whole table, each
        key's description and the user's comments above it, no blank line
        between keys; a blank line before each heading — [settings]'s first
        heading included, so the table line stays apart from its data. An entry's inline text is an end-of-line
        comment at one column per group (the run of keys between two
        headings) — or, when its value is too long or spans lines, a line
        above the key."""
        K = max([len(e.prefix) for e in entries if isinstance(e, _Entry)]
                or [0])

        def value_of(e: _Entry) -> Tuple[str, bool]:
            s = ("key", T, e.key)
            verbatim = (e.slot and not e.commented
                        and s in self.x.verbatim)
            return (self.x.verbatim[s] if verbatim else e.value), verbatim

        def base(e: _Entry) -> str:
            return f"{e.prefix.ljust(K)} = {value_of(e)[0]}"

        def fits(e: _Entry) -> bool:
            b, (v, verbatim) = base(e), value_of(e)
            return (not verbatim and "\n" not in v
                    and len(b) <= INLINE_MAX_COLUMN
                    and len(b) + 2 + 2 + len(e.inline) <= LINE_WIDTH)

        # The inline column of each group.
        col: Dict[int, int] = {}
        group: List[_Entry] = []

        def close_group() -> None:
            ok = [len(base(g)) for g in group if g.inline and fits(g)]
            for g in group:
                col[id(g)] = max(ok) + 2 if ok else 0
            group.clear()

        for e in entries:
            if isinstance(e, str):
                close_group()
            else:
                group.append(e)
        close_group()
        first = True
        for e in entries:
            if isinstance(e, str):
                if not (first and T) or T == ("settings",):
                    self.blank()
                self.emit(e)
                first = False
                continue
            first = False
            s = ("key", T, e.key)
            doc = _doc_lines(e.doc)
            tr = (self.x.trailing.get(s)
                  if e.slot and not e.commented else None)
            inline = e.inline and fits(e)
            if e.inline and not inline:
                doc.append(PROSE + e.inline)
            for ln in doc:
                self.emit(ln)
            if e.slot:
                self.user_runs(s)
            for ln in tr or ():
                self.emit(ln, template=False)
            value, verbatim = value_of(e)
            vlines = value.split("\n")
            pre = "# " if e.commented else ""
            first_line = f"{e.prefix.ljust(K)} = {vlines[0]}"
            if inline:
                first_line = f"{first_line.ljust(col[id(e)])}# {e.inline}"
            self.emit(first_line, template=not verbatim)
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
        inline = self.fill(_pick(k.inline, c), name)
        if k.name in values:
            return _Entry(k.name, toml_value(values[k.name], top=True), doc,
                          inline=inline)
        return _Entry(k.name, self.fill(_pick(k.value, c), name), doc,
                      commented=True, inline=inline)

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
        n_head = len(self.lines)
        self.unknown_top()
        self.end(())
        # The header sits directly on [settings] (a blank line only after
        # top-level lines that are not the header's).
        if len(self.lines) > n_head:
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
        spec = {k.name: k for k in SETTINGS_SPEC}
        entries: List[Union[_Entry, str]] = []
        for heading, names in SETTINGS_GROUPS:
            keys = _sorted_keys(spec[n] for n in names
                                if owned(c, "settings", n))
            if keys:
                entries.append(group_heading(heading))
                entries += [self.entry(k, s) for k in keys]
        self.keys(T, entries
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
                   self.fill(_pick(k.doc, c)), commented=True, slot=False,
                   inline=self.fill(_pick(k.inline, c)))
            for k in _account_order(ACCOUNT_SPEC)])
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
            # Compact: the reference block above documents every key.
            keys = _account_order(k for k in ACCOUNT_SPEC if k.name in acfg)
            self.keys(T, [_Entry(k.name, toml_value(acfg[k.name], top=True))
                          for k in keys]
                      + self.unknown_entries(acfg, spec_keys()["accounts"],
                                             f"accounts.{name}"))
            self.end(T)
        self.blank()
        for ln in textwrap.wrap(_MORE_ACCOUNTS_NOTE, 76):
            self.emit(PROSE + ln)

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
                      tz: Optional[str] = None,
                      grant_since: Optional[int] = None) -> Dict[str, Any]:
    """The parsed config `taxjson init` writes for `country`;
    `grant_since`: another year's option_grant_timing_since (Canada),
    written as set (else the key is left commented)."""
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
    if country == C.CANADA and grant_since is not None:
        # s.49(1) grant timing from the first year filed under it: kept
        # from another year's project (a later project must keep the
        # first value). Otherwise left commented: a new user who writes
        # no options needs none, and `taxjson run` says when the books
        # hold a written option and the key is not set.
        s["option_grant_timing_since"] = int(grant_since)
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
    ("Questrade", "Transaction history: every year available. It "
                  "downloads as Excel: convert it with taxjson-xlsx-to-csv."),
    ("RBC Direct Investing", "Transaction history, CSV: every year "
                             "available."),
    ("Webull", "Trading Summary, CSV: buys and sells only; enter "
               "dividends and interest from your slips."),
    ("Any other broker", "Any CSV, plus a column mapping "
                         "(docs/brokers.md, \"Any other broker\")."),
)
_CRYPTO_EXPORTS = (
    ("Kraken", "Trades AND Ledgers, CSV: both, every year available."),
    ("Coinbase", "Transaction history, CSV: every year available."),
)


def input_readme(country: str, name: str,
                 slips: str = "inputs/slips/",
                 kind: Optional[str] = None) -> str:
    """The README.txt `taxjson init` writes in inputs/<name>/: which
    export to download from each broker (all the history there is, plus
    a positions report), for the scaffold account `name`. `slips`: where
    the year's slips go (`YYYY/inputs/slips/` in a year folder: slips
    belong to one year). `kind`: taxable | sheltered | crypto (default:
    from the scaffold name)."""
    country = C.canonical_country(country)
    crypto = kind == "crypto" if kind else name == "crypto"
    sheltered = (kind == "sheltered" if kind
                 else name not in ("margin", "crypto"))
    slip = "T5008" if country == C.CANADA else "1099-B"
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
        "",
        "A ticker change or a journal between two listings that the",
        "export does not show is a dated event: one .tt line, date first",
        "(docs/settings.md, .tt files), e.g.",
        "  RENAME 2025-04-01 OLDQ.US NEWQ.US" if not crypto
        else "  RENAME 2025-04-01 OLDC NEWC",
        *(["  JOURNAL 2025-03-05 ABCX.TO ABCX.U.TO 100"] if not crypto
          else []),
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
            f"Your broker's {slip} slips are checked against the books by",
            *textwrap.wrap(f"`taxjson reconcile-slips` (keep each year's "
                           f"in {slips}).", 72),
        ]
    lines += [
        "",
        *textwrap.wrap(f"No such account? Delete this folder and its "
                       f"[accounts.{name}] section from taxjson.toml.", 72),
    ]
    return "\n".join(lines) + "\n"


def render_init(country: str, year: Optional[int] = None,
                tz: Optional[str] = None,
                extra: Optional[Dict[str, Any]] = None,
                grant_since: Optional[int] = None
                ) -> Tuple[str, Tuple[str, ...]]:
    """(taxjson.toml text, account names) for `taxjson init`; `extra`:
    [settings] values added (a year folder's inputs_dir ...);
    `grant_since`: scaffold_document's."""
    country = C.canonical_country(country)
    yr = int(year) if year is not None else _date.today().year
    doc = scaffold_document(country, yr, tz, grant_since)
    doc["settings"].update(extra or {})
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
    col: int = 0               # where the line's text starts (its indent)
    tcol: int = -1             # the column of its trailing comment's '#'


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
        indent = len(raw) - len(raw.lstrip())
        if st.startswith("#"):
            items.append(_Item("comment", li, text=st, col=indent))
            li += 1
            continue
        if st.startswith("["):
            path, aot, rest = _parse_header(st)
            rest = rest.strip()
            has = rest.startswith("#")
            items.append(_Item("header", li, text=st, path=path, aot=aot,
                               trailing=rest if has else None, col=indent,
                               tcol=(len(raw.rstrip()) - len(rest)
                                     if has else -1)))
            li += 1
            continue
        # key = value
        off = starts[li] + indent
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
        tcol = -1
        inner: List[str] = []
        vend = end
        for cs, ce in comments:
            if line_of(cs) == last_line and ce == end:
                trailing = text[cs:ce].rstrip()
                tcol = cs - starts[last_line]
                vend = cs
            else:
                inner.append(text[cs:ce].rstrip())
        value = text[vstart:vend].strip()
        items.append(_Item("kv", li, path=kp, trailing=trailing,
                           value=value, inner=bool(inner),
                           inner_comments=inner, end_line=last_line,
                           col=indent, tcol=tcol))
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


# A comment's text that starts like `key =` (a commented-out key, maybe
# one whose value goes on over the next comment lines).
_KEYISH = re.compile(r"""[A-Za-z0-9_\-"'. \t]+=""")
# How many comment lines a commented-out multi-line value may span.
_MAX_COMMENTED_VALUE_LINES = 60


def _body(line: str) -> str:
    """A comment line's text: what follows its leading '#'s."""
    return line.lstrip("#")


def _toml_code(text: str) -> bool:
    """`text` (comment text, '#'s dropped) is TOML: a key/value line or a
    table line (`[table]`, `[[table]]`), known key or not — or a line of
    a multi-line value on its own (an element `{ ... },` / `[ ... ],`, a
    closing `]`)."""
    b = text.strip()
    if not b or b.startswith("#"):
        return False
    if re.fullmatch(r"[\]}],?", b):
        return True
    if not (b.startswith(("[", "{")) or _KEYISH.match(b)):
        return False
    try:
        if tomllib.loads(text if "\n" in text else b):
            return True
    except Exception:  # noqa: BLE001 — any parse failure: not a line
        pass
    if "\n" in text or not b.startswith(("[", "{")):
        return False
    try:
        tomllib.loads(f"x = [\n{b}\n]")
    except Exception:  # noqa: BLE001 — not an element either: prose
        return False
    return True


def comment_line(line: str, code: bool) -> str:
    """A comment line in the file's convention: `# text` for commented-out
    TOML (`code`), `## text` (PROSE) for prose. The text is kept as it
    is: only the leading '#'s and one blank after them are replaced."""
    rest = _body(line)
    if rest.startswith(" "):
        rest = rest[1:]
    if not rest:
        return PROSE.rstrip()
    return ("# " if code else PROSE) + rest


def _comment_line1(line: str) -> str:
    """comment_line of a comment on its own (a trailing comment moved
    above its line, a comment inside a value): code when its text alone
    is TOML."""
    return comment_line(line, _toml_code(_body(line)))


def _code_lines(items: List[_Item], is_template
                ) -> Tuple[Set[int], Set[int]]:
    """(code, keep): the line numbers of the comment lines that are
    commented-out TOML — a line whose text is a key/value or a table
    line, or the lines of a commented-out multi-line value (`# paid = [`
    ... `# ]`: the text of the consecutive lines parses together); and
    of the lines of such a multi-line value holding a line of the
    user's, kept whole as the user's (the template's lines among them
    too) so the value is never split."""
    code: Set[int] = set()
    keep: Set[int] = set()
    n = len(items)
    i = 0
    while i < n:
        it = items[i]
        if it.kind != "comment":
            i += 1
            continue
        body = _body(it.text)
        if _toml_code(body):
            code.add(it.line)
            i += 1
            continue
        if _KEYISH.match(body.strip()):
            buf = [body]
            j = i + 1
            found = False
            while (j < n and j - i < _MAX_COMMENTED_VALUE_LINES
                   and items[j].kind == "comment"
                   and items[j].line == items[j - 1].line + 1):
                buf.append(_body(items[j].text))
                if _toml_code("\n".join(buf)):
                    found = True
                    break
                j += 1
            if found:
                group = [x.line for x in items[i:j + 1]]
                code.update(group)
                if not all(is_template(x.text) for x in items[i:j + 1]):
                    keep.update(group)
                i = j + 1
                continue
        i += 1
    return code, keep


def _continuation(it: _Item, owner: _Item, is_template
                  ) -> Optional[str]:
    """The comment line `it`, directly under `owner` (a key or table line
    with an end-of-line comment, or a continuation of one), as the text
    that continues that comment — or None when it is a comment line of
    its own (the next line's). A continuation is how the old aligned
    layout wrapped a long end-of-line comment:

        key = 1          # a long comment that
                         # goes on here
        #                #   or here (a '#' at column 0 keeps it a
        #                    comment line; the padding is alignment)

    so a continuation is a comment line indented past `owner`'s own
    start, one with a second '#' as padding (`#   #   text`), or one
    whose text is padded out to (about) the column of the comment it
    continues. A plain `# text` or `## text` at `owner`'s indent stays a
    comment line of its own (the next key's)."""
    st = it.text
    # The padding: the leading '#'s, blanks, and any further '#' after a
    # blank that is followed by a blank (`#   #   text`; `#123` is text;
    # `## text` is the prose mark, not padding).
    n = len(st)
    h = len(st) - len(st.lstrip("#"))
    i, double = h, False
    while True:
        while i < n and st[i] in " \t":
            i += 1
        if i < n and st[i] == "#" and (i + 1 == n or st[i + 1] in " \t"):
            i, double = i + 1, True
            continue
        break
    text = st[i:]
    if is_template(st) and (_COMMENTED_KEY.match(st)
                            or _commented_header(st) is not None):
        return None      # the template's commented-out key or table
    if (it.col > owner.col or double
            or (i - h >= 2 and it.col + i >= owner.tcol - 2)):
        return text
    return None


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
        h = _hash(n)
        return (not n or n in template or h in _LEGACY_TEMPLATE_HASHES
                or h in _LEGACY_TEMPLATE_HASHES_BY_COUNTRY[country])

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
    # Each of the user's comment lines is written in the convention:
    # commented-out TOML `# ...`, prose `## ...` (comment_line).
    code, keep_whole = _code_lines(items, is_template)
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

    def set_trailing(slot: Slot, text: str) -> List[str]:
        """The trailing comment above its line; its lines (to which
        continuation lines are added)."""
        kept.append(text)
        if slot in x.trailing:
            # Two lines fold into one (a sub-table and its key): the
            # second comment goes above the line.
            run = Run([text])
            x.runs.setdefault(slot, []).append(run)
            return run.lines
        x.trailing[slot] = [text]
        return x.trailing[slot]

    # The line whose trailing comment the comment lines directly under it
    # may continue (the old aligned layout wrapped a long end-of-line
    # comment onto `#` lines aligned under it), and where those lines go:
    # after the moved trailing comment of the user's; when the trailing
    # comment was the template's (regenerated), above the line on their
    # own; when the line has no place (a table written elsewhere), with
    # the next line that has one, like the trailing comment itself.
    cont: Optional[_Item] = None
    cont_lines: Optional[List[str]] = None
    cont_slot: Optional[Slot] = None

    def start_cont(it: _Item, slot: Optional[Slot],
                   lines: Optional[List[str]]) -> None:
        nonlocal cont, cont_lines, cont_slot
        cont = it if it.tcol >= 0 else None
        cont_lines, cont_slot = lines, slot

    def add_cont(line: str) -> None:
        nonlocal cont_lines
        if cont_slot is None:
            add_user(line)
            return
        if cont_lines is None:
            run = Run([])
            x.runs.setdefault(cont_slot, []).append(run)
            cont_lines = run.lines
        kept.append(line)
        cont_lines.append(line)

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
            cont = None
            flush_target(blank_after=True)
            gap = True
            # (A commented-out section lasts to the next table line,
            # blank lines included: its keys are blank-separated.)
            continue
        if it.kind == "comment":
            st = it.text
            tmpl = is_template(st) and it.line not in keep_whole
            own = comment_line(st, it.line in code)
            more = (_continuation(it, cont, is_template)
                    if cont is not None else None)
            if more is None:
                cont = None
            elif not tmpl:
                add_cont(_comment_line1("# " + more))
                continue
            # (else the template's own continuation, or a bare '#':
            # regenerated, or dropped)
            if not in_notes and not tmpl:
                # The user's own line, whatever it looks like (a
                # commented-out table too): it travels with the next
                # line that has a place in the template — or, when it is
                # a commented-out key of this table, with that key.
                add_user(own)
                m = _COMMENTED_KEY.match("#" + _body(st))
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
                if not tmpl:
                    add_user(own)
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
            lines = None
            if it.trailing and not is_template(it.trailing):
                if slot is None:
                    add_user(_comment_line1(it.trailing))
                else:
                    lines = set_trailing(slot,
                                         _comment_line1(it.trailing))
            start_cont(it, slot, lines)
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
            run = Run([_comment_line1(c) for c in it.inner_comments])
            kept.extend(run.lines)
            x.runs.setdefault(slot, []).append(run)
        lines = None
        if it.trailing and not is_template(it.trailing):
            lines = set_trailing(slot, _comment_line1(it.trailing))
        start_cont(it, slot, lines)
    flush_target()
    flush_end()
    return x, kept
