"""The taxjson.toml template: every key taxjson reads, documented, per country.

`taxjson init` writes it with the scaffold's values; `taxjson format`
re-renders an existing project's file into it with the user's values and
comments. One renderer serves both, so a fresh scaffold is already
formatted (formatting it is a no-op).

Layout. The sections come in a fixed order: [settings] (grouped),
the accounts (a reference block naming every account key, then each
[accounts.NAME] section in the user's order, then a commented example of
one more account), [estimate], [carryover], [[distributions]], and for
Canada [[capital_gains_dividends]] and [instalments]. A key that is set
is written active, in its template position, with its one-line
description as the trailing comment; a key that is not set is written
commented out, showing its default (or an example where it has none).
A table that is absent is written commented out. Only the keys the
project's country owns are listed (lib/country SETTING_COUNTRY /
CONFIG_COUNTRY / PLAN_COUNTRY): a Canadian file never mentions a US-only
key and the other way round.

The key lists come from the validator (lib/config_check: ACCOUNT_KEYS,
ESTIMATE_KEYS, ... and lib/country.SETTING_COUNTRY);
tests/test_config_template.py fails when a key the validator accepts has
no entry here.

`format_config(text)` keeps everything the file says:

- every value (the parsed TOML of the result must equal the input's,
  checked before anything is returned; account order and the order of
  [[...]] entries are kept);
- keys the template does not know, at the end of their table under a
  "Not in the template" line (a top-level key at the top, a table at the
  end of the file);
- comments: a trailing comment stays on its key or table line (it
  replaces the description there); a comment block stays directly above
  the key or table line that follows it (a commented-out key counts: the
  block goes above that key's template line); a block separated by a
  blank line from the next table goes to the end of the table it was in.
  Comment lines that are the template's own text — or text an earlier
  `taxjson init` wrote, recognised by hash — are regenerated rather than
  kept; a bare `#` line carries nothing and is dropped. A multi-line
  value with comments inside it is kept verbatim. Anything that cannot
  be placed goes to a "Your notes" block at the end of the file;
  nothing is dropped.
"""
from __future__ import annotations

import hashlib
import os
import re
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

# Comment column: a value is padded to this width so the descriptions
# line up (and two projects' files differ only where their values do).
COMMENT_COL = 22

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
    {source}, {tax_date}, {tz}, {name}). `doc` is the one-line
    description (a str, or {country: str}); `more` are continuation
    lines for the few keys whose description needs them."""
    name: str
    value: Text
    doc: Text
    more: Union[Tuple[str, ...], Mapping[str, Tuple[str, ...]]] = ()


def _pick(v: Any, country: str) -> Any:
    return v.get(country, v.get("*")) if isinstance(v, Mapping) else v


_DEFAULT_TZ = "America/Toronto"   # lib/brokerages/_crypto_common.DEFAULT_LOCAL_TZ


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
    "# Every key taxjson reads is listed here, grouped and column-aligned so",
    "# year-over-year projects diff cleanly:",
    "#   diff ~/taxes/{prev_year}/taxjson.toml ~/taxes/{year}/taxjson.toml",
    "# A commented key shows its default (or an example where it has none);",
    "# uncomment a line to change it. `taxjson format` puts an edited file",
    "# back into this layout, keeping your values and comments.",
)

# [settings], in groups: (heading lines, keys). A key the project's
# country does not own is left out (lib/country.SETTING_COUNTRY).
SETTINGS_GROUPS: Tuple[Tuple[Tuple[str, ...], Tuple[Key, ...]], ...] = (
    ((), (
        Key("year", "{year}", "tax year the pipeline reports on (required)"),
        Key("country", '"{country}"', "canada | ca | usa | us (required)"),
        Key("province", '"ON"',
            "{provinces} — `taxjson estimate` needs it (no default)"),
        Key("base_currency", '"{home}"',
            {"canada": "report currency: CAD (Bank of Canada rates)",
             "usa": "report currency: USD"}),
        Key("source_currencies", {"canada": '["USD"]', "usa": "[]"},
            "currencies you hold besides base_currency (FX rates fetched)"),
        Key("tax_date", '"{tax_date}"',
            {"canada": "settle | trade (default settle: CRA dates a sale "
                       "by settlement)",
             "usa": "trade | settle (default trade: the IRS dates a sale "
                    "by trade date)"}),
        Key("local_timezone", '"{tz}"',
            "zone crypto UTC times are dated in (IANA name; default {tz})"),
        Key("prior_year_record", '"../{prev_year}/filed/{prev_year}.json"',
            "last year's close-year record (`taxjson handoff`; no "
            "default)"),
    )),
    (("# Futures and foreign-currency cash:",), (
        Key("futures_settle", '"trade"',
            "trade | next_day: futures and futures options settle on the "
            "TRADE date",
            ("(daily variation margin); next_day = the clearing premium "
             "date",)),
        Key("fx_cash_gains", "false",
            {"canada": "true: end-of-run FX-on-cash report (s.39(1.1), "
                       "$200 de minimis)",
             "usa": "true: end-of-run FX-on-cash report (§988, ordinary "
                    "income)"}),
    )),
    (("# Income dating (README \"Income dating\"):",), (
        Key("foreign_return_of_capital", '"dividend"',
            "a non-Canadian issuer's return of capital (IB): \"dividend\" "
            "(s.90(1)) | \"acb\""),
        Key("corporate_distributions", '["XYZQ.TO"]',
            "Canadian issuers whose distributions are a corporation's (no "
            "default)"),
        Key("ric_january_dividends", '["XYZQ.US {next_year}-01-30"]',
            "January fund/REIT dividends taxed as received Dec 31 "
            "(IRC §852(b)(7) / §857(b)(9)):",
            ("\"SYMBOL\" (every January one) or \"SYMBOL YYYY-01-DD\" "
             "(that payment); no default",)),
    )),
    (("# Written-option premiums (ITA s.49(1)) — see "
      "`taxjson option-boundary`:",), (
        Key("option_premium_timing", '"grant"',
            "\"grant\": the premium is a gain in the year WRITTEN",
            ("\"close\": it is netted at the closing transaction instead",)),
        Key("option_grant_timing_since", "{year}",
            "contracts written before this year keep close timing "
            "(default: `year`)",
            ("SET ONCE to the first year you FILE under grant timing and "
             "keep it", "UNCHANGED in every later year's project (do not "
             "bump it with `year`)")),
        Key("option_buyback_loss_superficial", "false",
            "true: strict s.54 reading — a buy-back loss is superficial "
            "when identical",
            ("options are bought within 30 days and still held",)),
    )),
)

ACCOUNT_SPEC: Tuple[Key, ...] = (
    Key("type", '"taxable"', "REQUIRED: taxable | sheltered"),
    Key("plan", {"canada": '"rrsp"', "usa": '"ira"'},
        "the plan when the name doesn't say (default: from the name):",
        ("{plans}",)),
    Key("crypto", "false",
        "true: a Coinbase / Kraken account (crypto prices filled in by the "
        "run)"),
    Key("transfers", "false",
        "true: keep TRANSFER rows (contributions/withdrawals)"),
    Key("holdings", '["~/broker/{name}_holdings.toml"]',
        "positions files `taxjson sanity` reconciles against (default: "
        "none)"),
    Key("combined_broker_accounts", "false",
        "true: every broker account in these statements is yours, "
        "taxable together"),
    Key("exercise_fee", "1.00",
        "Webull: the exercise/assignment charge on the stock leg (no "
        "default:",
        ("without it no exercise/assignment is inferred — each candidate "
         "is named)",)),
    Key("brokerage", '"questrade"',
        "`taxjson fetch` source (taxjson-fetch plugin): questrade | "
        "ibkr_flex"),
    Key("account", '"12345678"',
        "questrade: the account number `taxjson fetch` downloads"),
    Key("query_id", '"123456"',
        "ibkr_flex: the Flex query id `taxjson fetch` runs"),
)

_ACCOUNTS_DOC = (
    "# One [accounts.NAME] section per folder under inputs/ (the folder name",
    "# is the account name). Each key, with its default:",
)

# The scaffold's accounts and the commented "one more account" example.
SCAFFOLD_ACCOUNTS = {
    "canada": ("margin", "tfsa", "rrsp", "crypto"),
    "usa": ("margin", "roth", "401k", "crypto"),
}
_MORE_ACCOUNT = {
    "canada": ("lira", ("# More accounts: one section per inputs/ folder, "
                        "e.g. a locked-in retirement account:",)),
    "usa": ("ira", ("# More accounts: one section per inputs/ folder, e.g. "
                    "a traditional IRA:",)),
}


@dataclass(frozen=True)
class Table:
    name: str
    aot: bool                      # an array of tables ([[name]])
    doc: Mapping[str, Tuple[str, ...]]
    keys: Tuple[Key, ...]


TABLES: Tuple[Table, ...] = (
    Table("estimate", False, {
        "canada": ("# Estimate inputs (`taxjson estimate`, and the "
                   "instalments current-year basis),",
                   "# used when the command-line flags aren't given:"),
        "usa": ("# Estimate inputs (`taxjson estimate`), used when the "
                "command-line flags",
                "# aren't given:")}, (
        Key("other_income", "0",
            "income besides these books (employment, interest ...)"),
        Key("other_losses", "0",
            {"canada": "net capital losses of earlier years applied, in "
                       "FULL dollars",
             "usa": "the SHORT-term capital loss carryover applied"}),
        Key("deductions", "0", "RRSP 20800, FHSA, RPP ... (full under AMT)"),
        Key("carrying_charges", "0", "line 22100 (50% under AMT)"),
        Key("amt_carryover", "{{ {prev_year} = 1200.50 }}",
            "minimum tax carryover by year of origin (T691; no default)"),
        Key("long_term_losses", "0",
            "the long-term capital loss carryover (Schedule D line 14)"),
    )),
    Table("carryover", False, {"*": (
        "# Losses actually applied on filed returns (`taxjson carryover`), "
        "by year:",)}, (
        Key("claimed", "{{ {prev_year} = 4000.00 }}",
            "net capital losses applied on each filed return (no "
            "default)"),
    )),
    Table("distributions", True, {"*": (
        "# Non-cash fund distributions (a reinvested capital-gains "
        "distribution,",
        "# a late return-of-capital factor) — `taxjson run` books each as "
        "a cost",
        "# adjustment on the shares held on the record date. One table "
        "each:")}, (
        Key("symbol", {"canada": '"XYZQ.TO"', "usa": '"XYZQ.US"'},
            "the books' symbol"),
        Key("record_date", "{year}-12-29", "the record date"),
        Key("per_share", "0.25",
            "base currency; negative = return of capital"),
    )),
    Table("capital_gains_dividends", True, {"*": (
        "# T5 box 18 capital-gains dividends the books carry as dividends "
        "(one",
        "# table per payment or year; `taxjson divs-sum`, the estimate):")}, (
        Key("symbol", '"ABCX.TO"', "the books' symbol"),
        Key("year", "{year}", "every dividend of that tax year — or:"),
        Key("date", "{year}-06-16", "one payment (instead of year)"),
        Key("amount", '"all"', "\"all\" or the box 18 amount, e.g. 1.25"),
        Key("account", '"margin"', "default: the taxable accounts"),
    )),
    Table("instalments", False, {"*": (
        "# Tax instalments (`taxjson instalments`, and a summary inside",
        "# `taxjson estimate`). Uncomment and fill in YOUR figures.")}, (
        Key("basis", '"current_year"', "{bases}"),
        Key("withheld", "0", "tax withheld at source this year"),
        Key("prior_year_net_tax", "55000",
            "last year's net tax owing (no default)",
            ("both years as CRA's instalment chart defines it: lines 42000 "
             "+ 42200",
             "+ 42800 (+ 43200) minus 43700 and the refundable credits — "
             "NOT line",
             "48500. Supply BOTH even on current_year: a 0 reads as \"I "
             "owed nothing\"")),
        Key("second_prior_net_tax", "41000",
            "the year before's net tax owing (no default)"),
        Key("prescribed_rate", "0.07",
            "CRA's overdue-tax rate (default: CRA's published rates)"),
        Key("prescribed_rates",
            "[\n  { from = \"{year}-01-01\", rate = 0.08 },\n"
            "  { from = \"{year}-07-01\", rate = 0.07 },\n]",
            "or a dated schedule: CRA resets the rate quarterly"),
        Key("paid",
            "[\n  { date = \"{year}-03-16\", amount = 15000 },\n"
            "  { date = \"{year}-05-20\", amount = 12000, "
            "note = \"refund transferred\" },\n]",
            "instalments paid (no default)"),
    )),
)

TABLE_BY_NAME = {t.name: t for t in TABLES}


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
    out = {"settings": tuple(k.name for _h, ks in SETTINGS_GROUPS
                             for k in ks),
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
# both countries): `taxjson format` regenerates them as template text
# instead of keeping them as the user's notes. Hashes, not text: the old
# examples are not carried in the source.
_LEGACY_TEMPLATE_HASHES = frozenset("""
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
""".split())


def _comment_suffixes(line: str) -> Iterable[str]:
    """_norm of the text from every '#' of a rendered template line (a
    whole comment line, a trailing description, a continuation)."""
    for m in re.finditer("#", line):
        n = _norm(line[m.start():])
        if n:
            yield n


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
            "{tz}": _DEFAULT_TZ,
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
        for run in self.x.runs.get(slot, ()):
            for ln in run.lines:
                self.emit(ln, template=False)
            if run.blank_after:
                self.lines.append("")

    def end(self, T: Tuple[Any, ...]) -> None:
        for run in self.x.runs.get(("end", T), ()):
            if run.blank_before:
                self.blank()
            for ln in run.lines:
                self.emit(ln, template=False)

    def header(self, T: Tuple[Any, ...], text: str, *,
               commented: bool = False) -> None:
        slot = ("hdr", T)
        self.table(T)
        self.user_runs(slot)
        line = ("# " if commented else "") + text
        tr = self.x.trailing.get(slot)
        if tr and not commented:
            self.emit(line + "  " + tr, template=False)
        else:
            self.emit(line)

    def key_line(self, T: Tuple[Any, ...], key: str, value: str, doc: str,
                 *, K: int, commented: bool, more: Sequence[str] = (),
                 slot: bool = True) -> None:
        col = K + 3 + COMMENT_COL
        s = ("key", T, key)
        if slot:
            self.user_runs(s)
        pre = "# " if commented else ""
        value = self.x.verbatim.get(s, value) if not commented else value
        vlines = value.split("\n")
        out = [f"{pre}{key_repr(key).ljust(K)} = {vlines[0]}"]
        out += [(pre + ln) if commented else ln for ln in vlines[1:]]
        tr = None if commented else self.x.trailing.get(s)
        comment = tr if tr else (f"# {doc}" if doc else "")
        user_line = len(out) - 1 if tr else None
        if comment:
            last = out[-1]
            out[-1] = last + " " * max(1, col - len(last)) + comment
        for i, ln in enumerate(out):
            # A verbatim value or a user's trailing comment is the
            # user's text, never matched as template text.
            self.emit(ln, template=(i != user_line
                                    and s not in self.x.verbatim))
        for m in more:
            self.emit("#" + " " * (col - 1) + "#   " + m)

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
        keys = [k for k, v in self.doc.items() if not self._is_table(k, v)]
        if not keys:
            return
        self.emit(_UNKNOWN_TOP_LINE)
        K = max(len(key_repr(k)) for k in keys)
        for k in keys:
            self.unknown.append(k)
            self.key_line((), k, toml_value(self.doc[k], top=True), "", K=K,
                          commented=False)

    def _is_table(self, k: str, v: Any) -> bool:
        if isinstance(v, dict):
            return True
        return (isinstance(v, list) and bool(v)
                and all(isinstance(e, dict) for e in v))

    def settings(self, s: Dict[str, Any]) -> None:
        c = self.country
        T = ("settings",)
        self.header(T, "[settings]")
        first = True
        for heading, keys in SETTINGS_GROUPS:
            keys = tuple(k for k in keys if owned(c, "settings", k.name))
            if not keys:
                continue
            if not first:
                self.lines.append("")
            first = False
            for h in heading:
                self.emit(self.fill(h))
            K = max(len(k.name) for k in keys)
            for k in keys:
                self._key(T, k, s, K)
        self._unknown_keys(T, s, spec_keys()["settings"], "settings",
                           owner_table="settings", blank=True)
        self.end(T)

    def _key(self, T, k: Key, values: Mapping[str, Any], K: int,
             name: str = "NAME") -> None:
        c = self.country
        doc = self.fill(_pick(k.doc, c), name)
        more = [self.fill(m, name) for m in _pick(k.more, c)]
        if k.name in values:
            self.key_line(T, k.name, toml_value(values[k.name], top=True),
                          doc, K=K, commented=False, more=more)
        else:
            self.key_line(T, k.name, self.fill(_pick(k.value, c), name),
                          doc, K=K, commented=True, more=more)

    def _unknown_keys(self, T, values: Mapping[str, Any],
                      known: Sequence[str], label: str, *,
                      owner_table: Optional[str] = None,
                      blank: bool = False) -> None:
        c = self.country
        extra = [k for k in values
                 if k not in known
                 or (owner_table and not owned(c, owner_table, k))]
        if not extra:
            return
        if blank:
            self.lines.append("")
        self.emit(_UNKNOWN_KEYS_LINE)
        K = max(len(key_repr(k)) for k in extra)
        for k in extra:
            self.unknown.append(f"[{label}] {k}")
            self.key_line(T, k, toml_value(values[k], top=True), "", K=K,
                          commented=False)

    def accounts(self, accts: Any, present: bool) -> None:
        c = self.country
        self.lines.append("")
        T0 = ("accounts",)
        self.table(T0)
        for ln in _ACCOUNTS_DOC:
            self.emit(ln)
        Kr = max(len(k.name) for k in ACCOUNT_SPEC)
        col = 4 + Kr + 3 + COMMENT_COL - 2
        for k in ACCOUNT_SPEC:
            head = (f"#   {k.name.ljust(Kr)} = "
                    f"{self.fill(_pick(k.value, c))}")
            self.emit(head + " " * max(1, col - len(head))
                      + "# " + self.fill(_pick(k.doc, c)))
            for m in _pick(k.more, c):
                self.emit("#" + " " * (col - 1) + "#   " + self.fill(m))
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
            keys = [k for k in ACCOUNT_SPEC if k.name in acfg]
            if keys:
                K = max(9, max(len(k.name) for k in keys))
                for k in keys:
                    self._key(T, k, acfg, K, name)
            self._unknown_keys(T, acfg, spec_keys()["accounts"],
                               f"accounts.{name}")
            self.end(T)
        more, intro = _MORE_ACCOUNT[c]
        if more not in names:
            T = ("accounts", more)
            self.lines.append("")
            for ln in intro:
                self.emit(ln)
            self.header(T, f"[accounts.{more}]", commented=True)
            self.key_line(T, "type", '"sheltered"', "", K=9,
                          commented=True)
            self.key_line(T, "transfers", "true",
                          self.fill(_pick(TRANSFERS_DOC, c)), K=9,
                          commented=True)
            self.end(T)

    def plain_table(self, t: Table, values: Any, present: bool) -> None:
        c = self.country
        T = (t.name,)
        for ln in _pick(t.doc, c):
            self.emit(self.fill(ln))
        self.header(T, f"[{t.name}]", commented=not present)
        keys = tuple(k for k in t.keys if owned(c, t.name, k.name))
        K = max(len(k.name) for k in keys)
        vals = values if isinstance(values, dict) else {}
        for k in keys:
            if present:
                self._key(T, k, vals, K)
            else:
                self.key_line(T, k.name, self.fill(_pick(k.value, c)),
                              self.fill(_pick(k.doc, c)), K=K,
                              commented=True,
                              more=[self.fill(m) for m in
                                    _pick(k.more, c)])
        self._unknown_keys(T, vals, [k.name for k in keys], t.name,
                           owner_table=t.name)
        self.end(T)

    def aot(self, t: Table, entries: Any) -> None:
        c = self.country
        T0 = (t.name,)
        for ln in _pick(t.doc, c):
            self.emit(self.fill(ln))
        self.header(T0, f"[[{t.name}]]", commented=True)
        K = max(len(k.name) for k in t.keys)
        for k in t.keys:
            self.key_line(T0, k.name, self.fill(_pick(k.value, c)),
                          self.fill(_pick(k.doc, c)), K=K, commented=True)
        self.end(T0)
        for i, e in enumerate(entries or []):
            T = (t.name, i)
            self.lines.append("")
            self.header(T, f"[[{t.name}]]")
            keys = [k for k in t.keys if k.name in e]
            Ke = max([len(k.name) for k in keys] or [1])
            for k in keys:
                self.key_line(T, k.name, toml_value(e[k.name], top=True), "",
                              K=Ke, commented=False)
            self._unknown_keys(T, e, [k.name for k in t.keys],
                               f"[{t.name}] #{i + 1}")
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
            if isinstance(v, dict):
                T = (k,)
                self.lines.append("")
                self.header(T, f"[{key_repr(k)}]")
                K = max([len(key_repr(x)) for x in v] or [1])
                for x, val in v.items():
                    self.key_line(T, x, toml_value(val, top=True), "", K=K,
                                  commented=False)
                self.end(T)
            else:
                for i, e in enumerate(v):
                    T = (k, i)
                    self.lines.append("")
                    self.header(T, f"[[{key_repr(k)}]]")
                    K = max([len(key_repr(x)) for x in e] or [1])
                    for x, val in e.items():
                        self.key_line(T, x, toml_value(val, top=True), "",
                                      K=K, commented=False)
                    self.end(T)

    @staticmethod
    def _shape_ok(name: str, v: Any) -> bool:
        t = TABLE_BY_NAME[name]
        if t.aot:
            return isinstance(v, list) and all(isinstance(e, dict)
                                               for e in v)
        return isinstance(v, dict)


TRANSFERS_DOC = "keep TRANSFER rows (contributions/withdrawals)"


# ------------------------------------------------------------ entry points

def scaffold_document(country: str, year: int,
                      tz: Optional[str] = None) -> Dict[str, Any]:
    """The parsed config `taxjson init` writes for `country`."""
    country = C.canonical_country(country)
    s: Dict[str, Any] = {
        "year": year, "country": country,
        "base_currency": C.home_currency(country),
        "source_currencies": ["USD" if country == C.CANADA else "CAD"],
        "tax_date": C.default_tax_date(country),
    }
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
    active: Tuple[Any, ...] = ()
    commented_ctx: Optional[Tuple[Any, ...]] = None
    aot_count: Dict[Tuple[Any, ...], int] = {}
    in_notes = False

    def add_user(line: str) -> None:
        nonlocal gap
        kept.append(line)
        if pending and not gap:
            pending[-1].lines.append(line)
        else:
            if pending and gap:
                pending[-1].blank_after = True
            pending.append(Run([line], blank_before=gap))
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

    for it in items:
        if it.kind == "blank":
            gap = True
            # A commented-out section ends at a blank line.
            commented_ctx = None
            continue
        if it.kind == "comment":
            st = it.text
            if st.strip() == NOTES_HEADING.strip():
                flush_end()
                in_notes = True
                gap = False
                continue
            if in_notes:
                if not is_template(st):
                    add_user(st)
                continue
            if not is_template(st):
                # The user's own line, whatever it looks like (a
                # commented-out key or table too): it travels with the
                # next line that has a place in the template.
                add_user(st)
                continue
            # The template's own line: regenerated, not kept — but a
            # commented-out table or key marks the place the comments
            # above it belong to.
            hdr = _commented_header(st)
            if hdr is not None:
                path, _aot = hdr
                slot = resolve(path)
                if slot is not None and slot[0] == "hdr":
                    before_header()
                    flush(slot)
                commented_ctx = path
                continue
            m = _COMMENTED_KEY.match(st)
            if m:
                ctx = commented_ctx if commented_ctx is not None else active
                slot = resolve(ctx + (m.group(1),))
                if slot is not None and slot[0] == "key":
                    flush(slot)
            continue
        if it.kind == "header":
            commented_ctx = None
            in_notes = False
            path = it.path
            if it.aot:
                n = aot_count.get(path, 0)
                aot_count[path] = n + 1
                path = path + (n,)
            slot = resolve(path)
            if slot is not None:
                before_header()
                flush(slot)
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
    flush_end()
    return x, kept
