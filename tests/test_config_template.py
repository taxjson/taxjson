"""The taxjson.toml template (lib/config_template): `taxjson init` writes
it, `taxjson format` re-lays an existing file into it.

- Completeness: every key the validator accepts (lib/config_check key
  lists, lib/country.SETTING_COUNTRY) is documented in each country's
  template — so a new key without template documentation fails here —
  and no key, table or plan kind owned by the OTHER country is
  mentioned (the Canada/USA partition).
- Layout: keys alphabetical in every table (active and commented in one
  sequence), one `=` column per table, each description on the lines
  above its key, a blank line between keys, no end-of-line comment.
- `taxjson format`: lossless (the parsed configuration is identical),
  idempotent, keeps unknown keys (flagged) and the user's comments;
  --check / --write / --no-backup; the refusal paths.

Synthetic values only.
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date as _date
from pathlib import Path
from unittest import mock

from taxjson.lib import config_template as CT
from taxjson.lib import country as C
from taxjson.lib.config_check import (ACCOUNT_KEYS, CARRYOVER_KEYS, CGD_KEYS,
                                      DISTRIBUTION_KEYS, ESTIMATE_KEYS,
                                      INSTALMENTS_KEYS, RETIRED_SETTINGS,
                                      TOP_LEVEL_TABLES)
from taxjson.lib.tomlcompat import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]


def _validator_keys():
    """{table: keys} straight from the validator's own tables."""
    return {
        "settings": [k for k in C.SETTING_COUNTRY
                     if k not in RETIRED_SETTINGS],
        "accounts": list(ACCOUNT_KEYS),
        "estimate": list(ESTIMATE_KEYS),
        "instalments": list(INSTALMENTS_KEYS),
        "carryover": list(CARRYOVER_KEYS),
        "distributions": list(DISTRIBUTION_KEYS),
        "capital_gains_dividends": list(CGD_KEYS),
    }


def _owner(table, key):
    if table == "settings":
        return C.SETTING_COUNTRY.get(key, C.BOTH)
    return (C.CONFIG_COUNTRY.get(f"[{table}]")
            or C.CONFIG_COUNTRY.get(f"[{table}] {key}", C.BOTH))


_HDR = re.compile(r"^#?\s*\[\[?([A-Za-z0-9_]+)")
_KEY = re.compile(r"^#?\s*([A-Za-z0-9_]+)\s*=")


def _mentioned(text):
    """{(table, key)} of every key line, active or commented, by the
    table it sits under."""
    out = set()
    table = None
    for line in text.splitlines():
        if "[accounts.NAME]" in line:
            table = "accounts"
            continue
        m = _HDR.match(line)
        if m:
            table = m.group(1)
            continue
        m = _KEY.match(line)
        if m and table:
            out.add((table, m.group(1)))
    return out


def _templates(country):
    """The init scaffold and the all-commented render (no accounts)."""
    init, _ = CT.render_init(country, 2025, tz="America/Toronto")
    bare = CT.render_document({"settings": {"country": country}}, country,
                              2025)
    return init, bare


class TestTemplateCompleteness(unittest.TestCase):
    def test_validator_tables_are_the_template_tables(self):
        self.assertEqual(set(_validator_keys()) - {"settings", "accounts"},
                         {t for t in TOP_LEVEL_TABLES
                          if t not in ("settings", "accounts")})

    def test_every_key_of_the_country_is_documented(self):
        for country in C.COUNTRIES:
            for text in _templates(country):
                seen = _mentioned(text)
                for table, keys in _validator_keys().items():
                    for key in keys:
                        if _owner(table, key) not in (C.BOTH, country):
                            continue
                        with self.subTest(country=country, key=key):
                            self.assertIn((table, key), seen,
                                          f"[{table}] {key} is not in the "
                                          f"{country} template — document "
                                          f"it in lib/config_template")

    def test_no_key_of_the_other_country_is_mentioned(self):
        for country in C.COUNTRIES:
            other = C.other_country(country)
            own = {k for t, ks in _validator_keys().items() for k in ks
                   if _owner(t, k) in (C.BOTH, country)}
            tables = {p[1:-1] for p, o in C.CONFIG_COUNTRY.items()
                      if o == other and p.endswith("]")}
            # Keys of the other country's settings / [estimate]: never
            # named. (A whole other-country table's keys are plain
            # words — symbol, date — checked as key lines below.)
            foreign = {k for t, ks in _validator_keys().items() for k in ks
                       if _owner(t, k) == other and t not in tables} - own
            plans = {k for k, o in C.PLAN_COUNTRY.items() if o == other}
            for text in _templates(country):
                self.assertFalse({(t, k) for t, k in _mentioned(text)
                                  if t in tables}, country)
                for word in sorted(foreign | tables | plans):
                    with self.subTest(country=country, word=word):
                        self.assertIsNone(
                            re.search(rf"(?<![\w.]){re.escape(word)}"
                                      rf"(?![\w])", text),
                            f"{word!r} ({other}) in the {country} "
                            f"template")

    def test_the_template_lists_only_known_keys(self):
        # The other direction: nothing documented that the validator
        # would warn about as unknown.
        known = _validator_keys()
        for table, keys in CT.spec_keys().items():
            for key in keys:
                self.assertIn(key, known[table], f"[{table}] {key}")



class TestInitScaffold(unittest.TestCase):
    def test_scaffold_effective_config(self):
        for country, accounts in (
                ("canada", ["margin", "tfsa", "rrsp", "crypto"]),
                ("usa", ["margin", "roth", "401k", "crypto"])):
            text, names = CT.render_init(country, 2025, tz="Europe/Paris")
            doc = tomllib.loads(text)
            self.assertEqual(list(doc), ["settings", "accounts"])
            self.assertEqual(list(doc["accounts"]), accounts)
            self.assertEqual(names, tuple(accounts))
            self.assertEqual(doc["accounts"]["crypto"],
                             {"type": "taxable", "crypto": True})
            s = doc["settings"]
            self.assertEqual(s["local_timezone"], "Europe/Paris")
            want = {"year": 2025, "country": country,
                    "base_currency": C.home_currency(country),
                    "tax_date": C.default_tax_date(country),
                    "local_timezone": "Europe/Paris"}
            if country == "canada":
                want["option_grant_timing_since"] = 2025
                # A US scaffold fetches no foreign rates by default
                # (an all-USD project needs none).
                want["source_currencies"] = ["USD"]
            self.assertEqual(s, want)

    def test_no_detected_zone_leaves_it_commented(self):
        text, _ = CT.render_init("canada", 2025, tz=None)
        self.assertNotIn("local_timezone", tomllib.loads(text)["settings"])
        self.assertRegex(text, r"(?m)^# local_timezone\s+= \"")

    def test_a_utc_system_zone_is_not_written(self):
        # A server/container/WSL clock on UTC says nothing about where the
        # user lives: the key stays commented instead.
        for z in ("UTC", "Etc/UTC", "Etc/GMT", "Zulu"):
            with mock.patch.dict(os.environ, {"TZ": z}):
                self.assertIsNone(CT.system_timezone(), z)

    def test_system_timezone_reads_tz_and_validates(self):
        with mock.patch.dict(os.environ, {"TZ": "America/Vancouver"}):
            self.assertEqual(CT.system_timezone(), "America/Vancouver")
        self.assertFalse(CT.valid_timezone("EST5EDT"))
        self.assertFalse(CT.valid_timezone("Not/AZone"))
        self.assertTrue(CT.valid_timezone("UTC"))

    def test_more_accounts_example_is_country_shaped(self):
        ca, _ = CT.render_init("canada", 2025)
        us, _ = CT.render_init("usa", 2025)
        self.assertIn("# [accounts.lira]", ca)
        self.assertIn("# [accounts.ira]", us)
        self.assertNotIn("# [accounts.crypto]", ca + us)

    def test_no_real_security_examples(self):
        # Placeholder tickers only (owner rule: no real security in a
        # template; the ticker.map stub too).
        from taxjson.bin.taxjson_run import _TEMPLATE_TICKER_MAP
        ca, _ = CT.render_init("canada", 2025)
        us, _ = CT.render_init("usa", 2025)
        for text in (ca, us, _TEMPLATE_TICKER_MAP):
            for sym in re.findall(r"\b([A-Z][A-Z0-9.-]*)\.(?:US|TO|V)\b",
                                  text):
                self.assertRegex(sym, r"^(XYZQ|ABCX|WXYQ|ZZZQ|OLDQ|NEWQ|"
                                      r"OLDCO|ABCX-B)", sym)


_MESSY_CA = '''\
# my notes about this year — top of file
[accounts.margin]   # the main account
type="taxable"
holdings = ["~/a.toml",  # first
   "~/b.toml"]   # second
mystery_key = 5

[settings]
country="ca"
year=2025
# province = "BC"
tax_date = 'settle'   # my reason
source_currencies=["USD","EUR"]
cross_asset = true

# a note at the end of settings

[[distributions]]
symbol="XYZQ.TO"
record_date=2025-12-29
per_share=0.2500
[[distributions]]
symbol = "ABCX.TO"   # second one
record_date = "2025-06-30"
per_share = -0.1

[estimate]
other_income = 1000
long_term_losses = 5

[accounts."my.acct"]
type = "sheltered"
plan = "tfsa"

[accounts.crypto]
type = "taxable"
crypto = true

[instalments]
basis = "prior_year"
paid = [ { date = "2025-03-15", amount = 100 }, { date = "2025-06-15", amount = 100 } ]

[other_table]
bar = { a = 1, b = [1,2] }
# the very last note
'''

_MESSY_US = '''\
[settings]
  country = "us"
year = 2025
local_timezone="America/New_York"
ric_january_dividends = ["XYZQ.US"]
province = "ON"

[accounts.401k]
type = "sheltered"
plan = "401k"
[accounts.margin]
# fetch it
brokerage = "questrade"
type = "taxable"
[estimate]
long_term_losses = 2
deductions = 1
[[capital_gains_dividends]]
symbol = "XYZQ.US"
year = 2025
amount = "all"
'''


class TestFormatConfig(unittest.TestCase):
    def test_init_scaffold_is_already_formatted(self):
        for country in C.COUNTRIES:
            for tz in (None, "America/Toronto"):
                text, _ = CT.render_init(country, 2025, tz=tz)
                r = CT.format_config(text)
                self.assertFalse(r.changed, f"{country} {tz}")
                self.assertEqual(r.unrecognised, [])
                self.assertEqual(r.notes_lines, 0)

    def _check(self, text):
        r = CT.format_config(text)
        before, after = tomllib.loads(text), tomllib.loads(r.text)
        self.assertEqual(after, before)
        self.assertEqual(list(after.get("accounts", {})),
                         list(before.get("accounts", {})))
        again = CT.format_config(r.text)
        self.assertFalse(again.changed, "not idempotent")
        return r

    def test_messy_canada_config(self):
        r = self._check(_MESSY_CA)
        out = r.text
        self.assertEqual(sorted(r.unrecognised),
                         sorted(["[settings] cross_asset",
                                 "[accounts.margin] mystery_key",
                                 "[estimate] long_term_losses",
                                 "[other_table]"]))
        # Every comment kept, next to what it was about.
        for c in ("# my notes about this year — top of file",
                  "# the main account", "# first", "# second",
                  '# province = "BC"', "# my reason",
                  "# a note at the end of settings", "# second one",
                  "# the very last note"):
            self.assertIn(c, out)
        # A commented-out key of the user's goes to that key (above the
        # template's own commented line), after its description.
        self.assertRegex(out, r'# ON \| BC.*\n# province = "BC"\n'
                              r'# province\s+= "ON"\n')
        # A trailing comment moves onto its own line above its line.
        self.assertRegex(out, r"# the main account\n\[accounts\.margin\]\n")
        self.assertRegex(out, r'# my reason\ntax_date\s+= "settle"\n')
        # The verbatim multi-line value keeps its inner comment.
        self.assertRegex(out, r'holdings\s+= \["~/a\.toml",  # first')
        # Canonical values: quoted string, a date as a date.
        self.assertRegex(out, r'(?m)^record_date = 2025-12-29$')
        self.assertRegex(out, r'(?m)^per_share\s+= 0\.25$')
        # Settings first, accounts in the user's order.
        self.assertLess(out.index("[settings]"),
                        out.index("[accounts.margin]"))
        self.assertLess(out.index("[accounts.margin]"),
                        out.index('[accounts."my.acct"]'))
        self.assertLess(out.index('[accounts."my.acct"]'),
                        out.index("[accounts.crypto]"))
        # Arrays of tables kept in order; unknown keys flagged.
        self.assertLess(out.index('"XYZQ.TO"'), out.index('"ABCX.TO"'))
        self.assertIn(CT._UNKNOWN_KEYS_LINE, out)
        self.assertIn("[other_table]", out)
        # The list of tables is laid out one per line.
        self.assertRegex(out, r'paid\s+= \[\n'
                              r'  \{ date = "2025-03-15", amount = 100 \},')

    def test_messy_us_config_keeps_canadian_keys_flagged(self):
        r = self._check(_MESSY_US)
        self.assertIn("[settings] province", r.unrecognised)
        self.assertIn("[estimate] deductions", r.unrecognised)
        self.assertIn("[capital_gains_dividends]", r.unrecognised)
        self.assertIn("# fetch it\nbrokerage", r.text)
        # The US template itself never documents a Canadian key.
        doc_part = r.text.split(CT._UNKNOWN_KEYS_LINE)[0]
        self.assertNotIn("option_premium_timing", doc_part)

    def test_old_layout_formats_without_notes(self):
        # A file laid out by an earlier scaffold (its prose and examples):
        # regenerated as template text, not collected as notes.
        old = (
            "# taxjson configuration — https://github.com/taxjson/taxjson\n"
            "#\n"
            "# Keys are grouped and column-aligned so year-over-year "
            "projects diff\n"
            "# cleanly:  diff ~/taxes/2025/taxjson.toml "
            "~/taxes/2026/taxjson.toml\n"
            "# Every commented key shows its default; uncomment a line to "
            "change it.\n\n"
            "[settings]\n"
            "year              = 2025\n"
            'country           = "canada"              # canada | ca | usa '
            "| us\n"
            "# fx_cash_gains = false               # true: end-of-run "
            "FX-on-cash report (s.39(1.1), $200 de minimis)\n\n"
            "[accounts.margin]\n"
            'type      = "taxable"          # taxable | sheltered\n')
        r = CT.format_config(old)
        self.assertEqual(r.notes_lines, 0)
        self.assertEqual(r.kept_comments, 0, r.text)

    def test_unplaceable_text_is_refused_not_lost(self):
        with self.assertRaises(CT.FormatError):
            CT.format_config("[settings]\nyear = 2025\n")       # no country
        with self.assertRaises(CT.FormatError):
            CT.format_config("[settings\ncountry = 'ca'\n")     # not TOML
        with self.assertRaises(CT.FormatError):
            CT.format_config('[settings]\ncountry = "ca"\n'
                             '[accounts]\nmargin = 1\n')

    def test_notes_block_round_trips(self):
        text, _ = CT.render_init("canada", 2025)
        text += "\n" + CT.NOTES_HEADING + "\n# kept for later\n"
        r = self._check(text)
        self.assertEqual(r.notes_lines, 1)
        self.assertTrue(r.text.rstrip().endswith("# kept for later"))


_TABLE_LINE = re.compile(r"^(?:# )?\[\[?[^\]]+\]\]?$")
_KEY_LINE = re.compile(r"^(# )?([A-Za-z0-9_-]+|\"[^\"]*\") *( = )")


def _layout(text):
    """The rendered file as tables: [(table line, [group, ...])], a group
    being the [(line index, key, commented, `=` column)] of its key
    lines (a "Not in the template" line starts a second group)."""
    out = [("<top>", [[]])]
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if _TABLE_LINE.match(ln):
            out.append((ln, [[]]))
            continue
        if ln in (CT._UNKNOWN_KEYS_LINE, CT._UNKNOWN_TOP_LINE):
            out[-1][1].append([])
            continue
        m = _KEY_LINE.match(ln)
        if m:
            out[-1][1][-1].append((i, m.group(2), bool(m.group(1)),
                                   m.start(3)))
    return [(t, gs) for t, gs in out if any(gs)]


def _full(country):
    """A document setting keys in every table (and unknown ones)."""
    ca = country == C.CANADA
    s = {"year": 2025, "country": country, "tax_date": "trade",
         "leaps_months": 12, "zz_unknown": 1, "aa_unknown": "x",
         "prior_year_record": "../2024/filed/a-rather-long-file-name-"
                              "for-the-record-of-the-prior-year.json"}
    doc = {"settings": s,
           "accounts": {
               "zeta": {"type": "taxable", "exercise_fee": 1.0,
                        "holdings": [f"~/broker/holdings_file_{i}.toml"
                                     for i in range(4)],
                        "brokerage": "questrade", "odd_key": True},
               "alpha": {"type": "sheltered", "transfers": True}},
           "estimate": {"other_income": 1000},
           "carryover": {"claimed": {"2024": 10.5}},
           "distributions": [
               {"symbol": "XYZQ.TO", "record_date": _date(2025, 12, 29),
                "per_share": 0.25},
               {"symbol": "ABCX.TO", "record_date": _date(2025, 6, 30),
                "per_share": -0.1}]}
    if ca:
        s["province"] = "ON"
        doc["instalments"] = {"basis": "prior_year", "paid": [
            {"date": _date(2025, 3, 15), "amount": 100}]}
        doc["capital_gains_dividends"] = [
            {"symbol": "ABCX.TO", "year": 2025, "amount": "all"}]
    return doc


def _renders():
    for country in C.COUNTRIES:
        init, bare = _templates(country)
        yield country, init
        yield country, bare
        yield country, CT.render_document(_full(country), country, 2025)


class TestLayout(unittest.TestCase):
    """Keys alphabetical per table (active and commented in one
    sequence), one `=` column per table, descriptions on the lines above
    the key, a blank line between keys, no end-of-line comment."""

    def test_keys_are_alphabetical_in_every_table(self):
        for country, text in _renders():
            for table, groups in _layout(text):
                for g in groups:
                    keys = [k for _i, k, _c, _col in g]
                    with self.subTest(country=country, table=table):
                        self.assertEqual(keys, sorted(keys))

    def test_one_equals_column_per_table(self):
        for country, text in _renders():
            for table, groups in _layout(text):
                cols = {col for g in groups for _i, _k, _c, col in g}
                with self.subTest(country=country, table=table):
                    self.assertEqual(len(cols), 1, cols)

    def test_mixed_active_and_commented_keys_interleave(self):
        text = CT.render_document(_full("canada"), "canada", 2025)
        settings = dict(_layout(text))["[settings]"]
        seq = [(k, c) for _i, k, c, _col in settings[0]]
        self.assertEqual([k for k, _c in seq],
                         sorted(k.name for k in CT.SETTINGS_SPEC
                                if CT.owned("canada", "settings", k.name)))
        flags = dict(seq)
        # commented, active, commented, active ... in one sequence
        self.assertTrue(flags["fx_cash_gains"])
        self.assertFalse(flags["leaps_months"])
        self.assertTrue(flags["local_timezone"])
        self.assertFalse(flags["prior_year_record"])
        # The keys taxjson does not read: sorted, after their line.
        self.assertEqual([k for _i, k, _c, _col in settings[1]],
                         ["aa_unknown", "zz_unknown"])

    def test_no_end_of_line_comments(self):
        for country, text in _renders():
            for ln in text.splitlines():
                if ln.startswith("#"):
                    continue
                with self.subTest(country=country, line=ln):
                    self.assertNotIn("#", ln)

    def test_description_above_each_key_and_a_blank_between_keys(self):
        for country, text in _renders():
            lines = text.split("\n")
            for table, groups in _layout(text):
                for g in groups:
                    for n, (i, key, _c, _col) in enumerate(g):
                        j = i
                        heads = (CT._UNKNOWN_KEYS_LINE, CT._UNKNOWN_TOP_LINE)
                        while j > 0 and lines[j - 1].startswith("# ") \
                                and not _TABLE_LINE.match(lines[j - 1]) \
                                and lines[j - 1] not in heads:
                            j -= 1
                        if j == i:
                            continue         # a key with no description
                        with self.subTest(country=country, table=table,
                                          key=key):
                            before = lines[j - 1]
                            if n == 0:
                                self.assertTrue(
                                    _TABLE_LINE.match(before)
                                    or before in heads,
                                    before)
                            else:
                                self.assertEqual(before, "")

    def test_description_lines_never_read_as_a_commented_key(self):
        known = set().union(*CT.spec_keys().values())
        for country, text in _renders():
            for ln in text.splitlines():
                m = re.match(r"# ([A-Za-z0-9_-]+)\s*=", ln)
                if m:
                    self.assertIn(m.group(1), known, ln)

    def test_long_descriptions_wrap(self):
        for country, text in _renders():
            for ln in text.splitlines():
                if ln.startswith("# ") and not _KEY_LINE.match(ln) \
                        and ln not in (CT._UNKNOWN_KEYS_LINE,
                                       CT._UNKNOWN_TOP_LINE,
                                       CT._UNKNOWN_TABLES_LINE,
                                       CT.NOTES_HEADING):
                    self.assertLessEqual(len(ln), CT.DOC_WIDTH + 2, ln)
        lines = CT._doc_lines("word " * 60)
        self.assertGreater(len(lines), 2)
        self.assertTrue(all(ln.startswith("# ") for ln in lines))

    def test_long_values_stay_whole(self):
        text = CT.render_document(_full("canada"), "canada", 2025)
        # A long string: one line, nothing after it.
        self.assertRegex(text, r'(?m)^prior_year_record\s+= "\.\./2024/'
                               r'filed/a-rather-long-[^"]*\.json"$')
        # A long list: one element per line, `=` still in the column.
        self.assertRegex(text, r'holdings\s+= \[\n  "~/broker/holdings_'
                               r'file_0\.toml",\n')
        self.assertFalse(CT.format_config(text).changed)

    def test_every_render_formats_to_itself(self):
        for country in C.COUNTRIES:
            text = CT.render_document(_full(country), country, 2025)
            r = CT.format_config(text)
            with self.subTest(country=country):
                self.assertFalse(r.changed)
                self.assertEqual(r.notes_lines, 0)
                self.assertEqual(r.kept_comments, 0)


# The owner's complaint, with synthetic values: keys added by hand at the
# top of [settings] and anywhere else, padding and `=` columns that do
# not line up, comments at different columns, keys in no order.
_HAND_EDITED = '''\
# taxjson configuration — https://github.com/taxjson/taxjson

[settings]
leaps_months = 12     # longer LEAPS for me
fx_cash_gains=true
year              = 2025                  # tax year the pipeline reports on (required)
country           = "canada"              # canada | ca | usa | us (required)
  province = "ON"                                          # Ontario
base_currency     = "CAD"                 # report currency: CAD (Bank of Canada rates)
source_currencies = ["USD"]               # currencies you hold besides base_currency (FX rates fetched)
tax_date          = "settle"              # settle | trade (default settle: CRA dates a sale by settlement)

# Report views:
# Futures and foreign-currency cash:
# futures_settle = "trade"             # trade | next_day: futures and futures options settle on the TRADE date
#                                      #   (daily variation margin); next_day = the clearing premium date

[accounts.margin]
year_end_posting = "06-30"
type      = "taxable"             # REQUIRED: taxable | sheltered
combined_broker_accounts=true
holdings = ["~/h/a.toml"]

[accounts.crypto]
type   = "taxable"   # REQUIRED: taxable | sheltered
crypto=true
'''

# A file the previous template wrote (grouped, descriptions at the end of
# the line, continuation lines), synthetic values.
_PREVIOUS_LAYOUT = '''\
# taxjson configuration — https://github.com/taxjson/taxjson
#
# Every key taxjson reads is listed here, grouped and column-aligned so
# year-over-year projects diff cleanly:
#   diff ~/taxes/2024/taxjson.toml ~/taxes/2025/taxjson.toml
# A commented key shows its default (or an example where it has none);
# uncomment a line to change it. `taxjson format` puts an edited file
# back into this layout, keeping your values and comments.

[settings]
year              = 2025                  # tax year the pipeline reports on (required)
country           = "canada"              # canada | ca | usa | us (required)
# province          = "ON"                # ON | BC | AB — `taxjson estimate` needs it (no default)
base_currency     = "CAD"                 # report currency: CAD (Bank of Canada rates)
tax_date          = "settle"              # settle | trade (default settle: CRA dates a sale by settlement)

# Report views:
# leaps_months = 9                   # LEAPS views (leaps, leaps-sum): a long option bought more than this many
#                                    #   months before expiry (default 9; no effect on any tax figure)

# Written-option premiums (ITA s.49(1)) — see `taxjson option-boundary`:
# option_premium_timing           = "grant"             # "grant": the premium is a gain in the year WRITTEN
#                                                       #   "close": it is netted at the closing transaction instead
option_grant_timing_since       = 2025                  # contracts written before this year keep close timing (default: `year`)
#                                                       #   SET ONCE to the first year you FILE under grant timing and keep it
#                                                       #   UNCHANGED in every later year's project (do not bump it with `year`)

# One [accounts.NAME] section per folder under inputs/ (the folder name
# is the account name). Each key, with its default:
#   type                     = "taxable"           # REQUIRED: taxable | sheltered
#   transfers                = false               # true: keep TRANSFER rows (contributions/withdrawals)

[accounts.margin]
type      = "taxable"             # REQUIRED: taxable | sheltered

# More accounts: one section per inputs/ folder, e.g. a locked-in retirement account:
# [accounts.lira]
# type      = "sheltered"
# transfers = true                # keep TRANSFER rows (contributions/withdrawals)

# Tax instalments (`taxjson instalments`, and a summary inside
# `taxjson estimate`). Uncomment and fill in YOUR figures.
[instalments]
basis                = "prior_year"        # current_year | prior_year | cra_reminder
prior_year_net_tax   = 100                 # last year's net tax owing (no default)
#                                          #   both years as CRA's instalment chart defines it: lines 42000 + 42200
#                                          #   + 42800 (+ 43200) minus 43700 and the refundable credits — NOT line
#                                          #   48500. Supply BOTH even on current_year: a 0 reads as "I owed nothing"
'''


class TestFormatToCanonicalLayout(unittest.TestCase):
    def test_hand_edited_file_formats_to_the_canonical_layout(self):
        r = CT.format_config(_HAND_EDITED)
        doc = tomllib.loads(_HAND_EDITED)
        self.assertEqual(tomllib.loads(r.text), doc)
        self.assertFalse(CT.format_config(r.text).changed, "idempotent")
        self.assertEqual(r.notes_lines, 0)
        # The user's two trailing comments, each on its own line just
        # above its key (after the key's description) ...
        self.assertRegex(r.text, r"\n# longer LEAPS for me\n"
                                 r"leaps_months +=")
        self.assertRegex(r.text, r"\n# Ontario\nprovince +=")
        # ... and otherwise exactly the template filled with the values:
        # the old descriptions and group headings are regenerated.
        mine = {"# longer LEAPS for me", "# Ontario"}
        self.assertEqual(
            "\n".join(ln for ln in r.text.split("\n") if ln not in mine),
            CT.render_document(doc, "canada"))
        self.assertEqual(r.kept_comments, 2)

    def test_previous_layout_is_regenerated_without_notes(self):
        r = CT.format_config(_PREVIOUS_LAYOUT)
        self.assertEqual(r.notes_lines, 0)
        self.assertEqual(r.kept_comments, 0, r.text)
        doc = tomllib.loads(_PREVIOUS_LAYOUT)
        self.assertEqual(r.text, CT.render_document(doc, "canada"))

    def test_trailing_comment_moves_above_and_stays(self):
        text = ('[settings]\ncountry = "ca"  # mine\nyear = 2025\n'
                '[accounts.margin]   # main\ntype = "taxable"\n')
        r = CT.format_config(text)
        self.assertRegex(r.text, r"\n# mine\ncountry +=")
        self.assertRegex(r.text, r"\n# main\n\[accounts\.margin\]\n")
        self.assertFalse(CT.format_config(r.text).changed)
        for ln in r.text.splitlines():
            if not ln.startswith("#"):
                self.assertNotIn("#", ln)

    def test_comments_anywhere_format_idempotently(self):
        # Comment lines, blank lines, commented-out keys and tables and
        # trailing comments dropped at random places (outside multi-line
        # values): formatting keeps every one and a second format
        # changes nothing.
        import random
        rnd = random.Random(20261004)
        bases = [CT.render_document(_full(c), c, 2025)
                 for c in C.COUNTRIES] + [_PREVIOUS_LAYOUT, _MESSY_CA]
        extra = ['# note', '', '# province = "BC"', '# leaps_months = 4',
                 '# type = "x"', '# [accounts.old]']
        for n in range(160):
            lines = rnd.choice(bases).split("\n")
            for _ in range(rnd.randint(1, 6)):
                i = rnd.randrange(len(lines))
                if lines[i - 1].rstrip().endswith(("[", ",")) \
                        or lines[i].lstrip().startswith(("{", "]", '"')):
                    continue
                if rnd.random() < 0.2 and re.match(r"[a-z_]+ += [^\[{]*$",
                                                    lines[i]):
                    lines[i] += "  # tr"
                else:
                    lines.insert(i, rnd.choice(extra))
            text = "\n".join(lines)
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                continue
            with self.subTest(n=n):
                r = CT.format_config(text)
                self.assertEqual(tomllib.loads(r.text), tomllib.loads(text))
                self.assertFalse(CT.format_config(r.text).changed)

    def test_users_commented_key_joins_its_key(self):
        # Written above the wrong key, it moves to its own key's place.
        text = ('[settings]\ncountry = "ca"\n# was:\n'
                '# leaps_months = 6\n\nyear = 2025\n# tax_date = "trade"\n'
                'leaps_months = 3\n')
        r = CT.format_config(text)
        self.assertRegex(r.text, r'\n# was:\n# leaps_months = 6\n'
                                 r'leaps_months +=')
        self.assertRegex(r.text, r'\n# tax_date = "trade"\n'
                                 r'# tax_date +=')
        self.assertFalse(CT.format_config(r.text).changed)


def _cli(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


# What every command loads (no other-country key): the CLI's config.
_MESSY_CA_VALID = _MESSY_CA.replace("long_term_losses = 5\n", "")


class TestFormatCommand(unittest.TestCase):
    def _project(self, td, text=_MESSY_CA_VALID):
        root = Path(td)
        (root / "taxjson.toml").write_text(text, encoding="utf-8")
        return root

    def test_dry_run_check_and_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            cfg = root / "taxjson.toml"
            os.chmod(cfg, 0o644)
            r = _cli(root, "format")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("+++ taxjson.toml (formatted)", r.stdout)
            self.assertIn("mystery_key", r.stderr)          # flagged
            self.assertEqual(cfg.read_text(encoding="utf-8"),
                             _MESSY_CA_VALID)
            r = _cli(root, "format", "--check")
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout, "")
            r = _cli(root, "format", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            bak = root / "taxjson.toml.bak"
            self.assertEqual(bak.read_text(encoding="utf-8"),
                             _MESSY_CA_VALID)
            self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o644)
            self.assertEqual(tomllib.loads(cfg.read_text(encoding="utf-8")),
                             tomllib.loads(_MESSY_CA_VALID))
            r = _cli(root, "format", "--check")
            self.assertEqual(r.returncode, 0, r.stderr)
            # A second --write changes nothing and makes no backup.
            os.unlink(bak)
            r = _cli(root, "format", "--write")
            self.assertEqual(r.returncode, 0)
            self.assertIn("already formatted", r.stdout)
            self.assertFalse(bak.exists())

    def test_write_no_backup(self):
        # (A config every command refuses — the other country's keys —
        # is refused by format too, with the same message.)
        valid_us = "\n".join(
            ln for ln in _MESSY_US.splitlines()
            if not ln.startswith(("province", "deductions"))
        ).split("[[capital_gains_dividends]]")[0]
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, valid_us)
            r = _cli(root, "format", "--write", "--no-backup")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((root / "taxjson.toml.bak").exists())
            r = _cli(root, "format", "--no-backup")
            self.assertEqual(r.returncode, 2)

    def test_formatted_project_still_loads_the_same(self):
        from taxjson.bin.taxjson_run import load_config
        with tempfile.TemporaryDirectory() as td:
            # (a scaffold with a crypto account names its zone)
            root = self._project(td, CT.render_init(
                "canada", 2025, tz="America/Toronto")[0]
                                 .replace("[accounts.margin]\n",
                                          "[accounts.margin]\n# mine\n"))
            before = load_config(root)
            self.assertEqual(_cli(root, "format", "--write").returncode, 0)
            self.assertEqual(load_config(root), before)

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "[settings]\nyear = 2025\n")
            r = _cli(root, "format", "--write")
            # (the config check every command runs: exit 1)
            self.assertIn(r.returncode, (1, 2))
            self.assertIn("country", r.stderr)
            self.assertEqual((root / "taxjson.toml").read_text(),
                             "[settings]\nyear = 2025\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            (root / "distributions.map").write_text("XYZQ.TO 2025-12-29 1\n")
            r = _cli(root, "format", "--write")
            self.assertEqual(r.returncode, 2)
            self.assertIn("taxjson migrate", r.stderr)
            self.assertEqual((root / "taxjson.toml").read_text(),
                             _MESSY_CA_VALID)

    def test_command_is_in_the_set_up_group(self):
        from taxjson.bin.taxjson_run import _COMMAND_GROUPS
        self.assertIn("format", dict(_COMMAND_GROUPS)["Set up"])


if __name__ == "__main__":
    unittest.main()
