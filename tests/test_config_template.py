"""The taxjson.toml template (lib/config_template): `taxjson init` writes
it, `taxjson format` re-lays an existing file into it.

- Completeness: every key the validator accepts (lib/config_check key
  lists, lib/country.SETTING_COUNTRY) is documented in each country's
  template — so a new key without template documentation fails here —
  and no key, table or plan kind owned by the OTHER country is
  mentioned (the Canada/USA partition).
- Layout: [settings] in groups (each key in exactly one, a heading line
  per group, keys alphabetical within it, a blank line between groups);
  every other table's keys alphabetical (an account table's `type`
  first), active and commented in one sequence; one `=` column per
  table; each description on the lines above its key, no blank line
  between keys; [accounts.NAME] tables compact (no descriptions: the
  reference block documents every account key); the only end-of-line
  comment is a key's inline text (its values), aligned per group; no
  fetch-plugin broker named.
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
_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "config_template"


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

    def test_only_the_scaffold_accounts_no_example_account(self):
        # The owner: init shows margin/tfsa/rrsp/crypto (US: margin/roth/
        # 401k/crypto) and nothing else — no commented example account,
        # just a note on adding one.
        for country, names in CT.SCAFFOLD_ACCOUNTS.items():
            text, accts = CT.render_init(country, 2025)
            self.assertEqual(tuple(accts), names)
            self.assertNotRegex(text, r"(?m)^#\s*\[accounts\.(?!NAME\])")
            self.assertIn("More accounts: add an [accounts.NAME] table", text)

    def test_a_previous_example_account_is_regenerated_not_kept(self):
        # A file an earlier init wrote with the commented LIRA example
        # formats without it: template text, not the user's notes.
        old = CT.render_init("canada", 2025)[0].replace(
            "# Estimate inputs",
            "# More accounts: one table per inputs/ folder, e.g. a locked-in "
            "retirement\n# account:\n# [accounts.lira]\n"
            "# true: keep TRANSFER rows (contributions/withdrawals).\n"
            "# transfers = true\n\n# REQUIRED: taxable | sheltered.\n"
            "# type      = \"sheltered\"\n\n# Estimate inputs", 1)
        self.assertIn("# [accounts.lira]", old)
        r = CT.format_config(old)
        self.assertNotIn("[accounts.lira]", r.text)
        self.assertNotIn("locked-in", r.text)
        self.assertEqual((r.notes_lines, r.kept_comments), (0, 0))

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
        # (prose as `## text`, a commented-out key as `# key = value`;
        # the comments inside a multi-line value as written)
        for c in ("## my notes about this year — top of file",
                  "## the main account", "# first", "# second",
                  "## my reason", "## a note at the end of settings",
                  "## second one", "## the very last note"):
            self.assertIn(c, out)
        self.assertRegex(out, r'(?m)^# province = "BC"$')
        # A commented-out key of the user's goes to that key (above the
        # template's own commented line), after its description.
        self.assertRegex(out, r'## The province .*\n# province = "BC"\n'
                              r'# province\s+= "ON" +# ON \| BC')
        # A trailing comment moves onto its own line above its line.
        self.assertRegex(out, r"\n## the main account\n\[accounts\.margin\]\n")
        self.assertRegex(out, r'\n## my reason\ntax_date\s+= "settle" +# settle')
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
        self.assertIn("\n## fetch it\nbrokerage", r.text)
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
        self.assertTrue(r.text.rstrip().endswith("\n## kept for later"))


_TABLE_LINE = re.compile(r"^(?:# )?\[\[?[^\]]+\]\]?$")
_GROUP_LINES = {CT.group_heading(g) for g, _keys in CT.SETTINGS_GROUPS}
# A key line ending in an inline comment: (prefix, key, text).
_INLINE = re.compile(r"^((?:# )?([A-Za-z0-9_-]+) *= .*?\S)  +# (.+)$")


def _inlines(country):
    """{key: filled inline text} of every key that has one."""
    r = CT._Renderer({}, country, 2025)
    keys = list(CT.SETTINGS_SPEC) + list(CT.ACCOUNT_SPEC) + [
        k for t in CT.TABLES for k in t.keys]
    return {k.name: r.fill(CT._pick(k.inline, country)) for k in keys
            if CT._pick(k.inline, country)}
_KEY_LINE = re.compile(r"^(# )?([A-Za-z0-9_-]+|\"[^\"]*\") *( = )")


def _layout(text):
    """The rendered file as tables: [(table line, [group, ...])], a group
    being the [(line index, key, commented, `=` column)] of its key
    lines (a [settings] group heading or a "Not in the template" line
    starts a new group)."""
    out = [("<top>", [[]])]
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if _TABLE_LINE.match(ln):
            out.append((ln, [[]]))
            continue
        if ln in (CT._UNKNOWN_KEYS_LINE, CT._UNKNOWN_TOP_LINE) \
                or ln in _GROUP_LINES:
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
                        "brokerage": "examplefetch", "odd_key": True},
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


def _is_account(table):
    return table == "# [accounts.NAME]" or table.startswith("[accounts.")


def _expected_order(table, keys):
    """Alphabetical; an account table's `type` first."""
    if _is_account(table):
        return sorted(keys, key=lambda k: (k != "type", k))
    return sorted(keys)


class TestLayout(unittest.TestCase):
    """[settings] in groups, every other table's keys alphabetical (an
    account's `type` first; active and commented in one sequence), one
    `=` column per table, descriptions on the lines above the key, no
    blank line between keys, compact account tables, end-of-line comments
    only for a key's inline text."""

    def test_keys_are_in_order_in_every_table_and_group(self):
        for country, text in _renders():
            for table, groups in _layout(text):
                for g in groups:
                    keys = [k for _i, k, _c, _col in g]
                    with self.subTest(country=country, table=table):
                        self.assertEqual(keys, _expected_order(table, keys))

    def test_one_equals_column_per_table(self):
        for country, text in _renders():
            for table, groups in _layout(text):
                cols = {col for g in groups for _i, _k, _c, col in g}
                with self.subTest(country=country, table=table):
                    self.assertEqual(len(cols), 1, cols)

    def test_mixed_active_and_commented_keys_interleave(self):
        text = CT.render_document(_full("canada"), "canada", 2025)
        settings = dict(_layout(text))["[settings]"]
        seq = [(k, c) for g in settings[:-1] for _i, k, c, _col in g]
        want = [n for _h, names in CT.SETTINGS_GROUPS
                for n in sorted(names) if CT.owned("canada", "settings", n)]
        self.assertEqual([k for k, _c in seq], want)
        flags = dict(seq)
        # commented, active, commented, active ... in one sequence
        self.assertTrue(flags["fx_cash_gains"])
        self.assertFalse(flags["leaps_months"])
        self.assertTrue(flags["local_timezone"])
        self.assertFalse(flags["prior_year_record"])
        # The keys taxjson does not read: sorted, after their line.
        self.assertEqual([k for _i, k, _c, _col in settings[-1]],
                         ["aa_unknown", "zz_unknown"])

    def test_only_a_keys_inline_text_ends_a_line(self):
        # An end-of-line comment is a key's inline text (its values, or
        # what `true` means) — nothing else, and never in an account
        # table (compact) or after a description.
        for country, text in _renders():
            inl = _inlines(country)
            for ln in text.splitlines():
                m = _INLINE.match(ln)
                if not ln.startswith("#"):
                    with self.subTest(country=country, line=ln):
                        if "#" in ln:
                            self.assertTrue(m, ln)
                            self.assertEqual(m.group(3), inl[m.group(2)])
                elif m and _KEY_LINE.match(ln):
                    with self.subTest(country=country, line=ln):
                        self.assertEqual(m.group(3), inl[m.group(2)])
            for m in re.finditer(r"(?m)^\[accounts\.[^\]]+\]\n((?:.+\n)*)",
                                 text):
                self.assertNotIn("#", m.group(1))

    def test_listed_values_render_inline_and_aligned(self):
        for country, text in _renders():
            inl = _inlines(country)
            lines = text.split("\n")
            for table, groups in _layout(text):
                for g in groups:
                    cols = set()
                    for i, key, _c, _col in g:
                        if table.startswith(("[accounts.", "[[")):
                            continue     # compact: no comments
                        if key not in inl or len(lines[i]) > 100:
                            continue
                        m = _INLINE.match(lines[i])
                        with self.subTest(country=country, line=lines[i]):
                            if m:
                                cols.add(m.start(3) - 2)
                            else:
                                # Pushed to the line above (a long value).
                                self.assertIn(f"## {inl[key]}",
                                              lines[:i][-6:])
                    with self.subTest(country=country, table=table):
                        self.assertLessEqual(len(cols), 1, cols)
        ca, _ = CT.render_init("canada", 2025)
        self.assertRegex(ca, r'(?m)^tax_date +="settle" +# settle \| trade$'
                         .replace('="', '= "'))
        self.assertRegex(ca, r'(?m)^country +=.*  # canada \| ca \| usa \| us')
        self.assertRegex(ca, r'(?m)^# type +=.*  # taxable \| sheltered')
        us, _ = CT.render_init("usa", 2025)
        self.assertRegex(us, r'(?m)^tax_date +="trade" +# trade \| settle$'
                         .replace('="', '= "'))
        # One column per group: tax_date's and country's comments line up.
        col = {ln.index("  # ") for ln in ca.split("\n")
               if ln.startswith(("country ", "tax_date ", "# province "))}
        self.assertEqual(len(col), 1, col)

    def test_a_long_value_puts_its_inline_text_above(self):
        doc = {"settings": {"country": "canada", "year": 2025,
                            "province": "ON" * 30}}
        text = CT.render_document(doc, "canada", 2025)
        self.assertRegex(text, r"\n## ON \| BC \| AB\nprovince += \"(ON)+\"\n")
        # ... and the other keys of the group keep their column.
        self.assertRegex(text, r'(?m)^country +=.*  # canada')
        r = CT.format_config(text)
        self.assertFalse(r.changed)
        self.assertEqual((r.notes_lines, r.kept_comments), (0, 0))
        # A short value again: the text goes back to the line's end.
        r = CT.format_config(text.replace("ON" * 30, "BC"))
        self.assertRegex(r.text, r'(?m)^province +=.*"BC" +# ON \| BC \| AB$')
        self.assertEqual((r.notes_lines, r.kept_comments), (0, 0))

    def test_description_above_each_key_no_blank_between_keys(self):
        heads = (CT._UNKNOWN_KEYS_LINE, CT._UNKNOWN_TOP_LINE)
        for country, text in _renders():
            lines = text.split("\n")
            for table, groups in _layout(text):
                for g in groups:
                    for n, (i, key, _c, _col) in enumerate(g):
                        j = i
                        while j > 0 and lines[j - 1].startswith("## ") \
                                and not _KEY_LINE.match(lines[j - 1]) \
                                and not _TABLE_LINE.match(lines[j - 1]) \
                                and lines[j - 1] not in heads \
                                and lines[j - 1] not in _GROUP_LINES:
                            j -= 1
                        before = lines[j - 1]
                        with self.subTest(country=country, table=table,
                                          key=key, before=before):
                            if n == 0:
                                # Under its table line or its heading.
                                self.assertTrue(
                                    _TABLE_LINE.match(before)
                                    or before in heads
                                    or before in _GROUP_LINES, before)
                            else:
                                # Directly under the previous key's value
                                # (a multi-line value ends in `]`).
                                self.assertNotEqual(before, "")
                                self.assertTrue(
                                    _KEY_LINE.match(before)
                                    or before.lstrip("# ") in ("]", "}")
                                    or before.startswith(("  ", "#   ")),
                                    before)

    def test_no_blank_line_inside_a_table_or_group(self):
        # A blank line only before a [settings] group heading (and a
        # "Not in the template" line), never between two keys.
        for country, text in _renders():
            lines = text.split("\n")
            for i, ln in enumerate(lines[1:-1], 1):
                if ln:
                    continue
                prev, nxt = lines[i - 1], lines[i + 1]
                if not (_KEY_LINE.match(prev) or prev.lstrip("# ") in
                        ("]", "}")):
                    continue
                with self.subTest(country=country, line=i, next=nxt):
                    # After a key: what follows is a new group, a new
                    # table (its heading or table line) or a note.
                    self.assertFalse(_KEY_LINE.match(nxt), nxt)
                    if nxt.startswith("## ") and not (
                            nxt in _GROUP_LINES or _TABLE_LINE.match(nxt)
                            or nxt in (CT._UNKNOWN_KEYS_LINE,
                                       CT._UNKNOWN_TOP_LINE,
                                       CT._UNKNOWN_TABLES_LINE)):
                        # A table heading (description) is followed, within
                        # its paragraph, by its table line.
                        k = i + 1
                        while lines[k].startswith("## ") \
                                and not _TABLE_LINE.match(lines[k]):
                            k += 1
                        self.assertTrue(
                            _TABLE_LINE.match(lines[k])
                            or lines[k] == "" and
                            lines[k - 1].startswith("## More accounts")
                            or lines[k] == "" and
                            lines[k - 1].endswith("folder for it."),
                            lines[i + 1:k + 1])

    def test_settings_groups_partition_the_settings(self):
        names = [n for _h, keys in CT.SETTINGS_GROUPS for n in keys]
        self.assertEqual(len(names), len(set(names)), "a key in two groups")
        self.assertEqual(sorted(names),
                         sorted(k.name for k in CT.SETTINGS_SPEC),
                         "every [settings] key in exactly one group")
        # (and so every setting the validator accepts: the completeness
        # test above ties SETTINGS_SPEC to it)
        self.assertEqual(set(names), set(_validator_keys()["settings"]))

    def test_group_headings_in_order_with_their_keys(self):
        for country in C.COUNTRIES:
            init, bare = _templates(country)
            for text in (init, bare,
                         CT.render_document(_full(country), country, 2025)):
                settings = text.split("[settings]\n", 1)[1].split(
                    "# [accounts.NAME]")[0]
                lines = settings.split("\n")
                # The first group heading directly under [settings].
                self.assertEqual(lines[0], CT.group_heading("Project"))
                want = [(h, sorted(n for n in keys
                                   if CT.owned(country, "settings", n)))
                        for h, keys in CT.SETTINGS_GROUPS]
                want = [(CT.group_heading(h), ks) for h, ks in want if ks]
                got = []
                for ln in lines:
                    if ln in _GROUP_LINES:
                        got.append((ln, []))
                    elif ln in (CT._UNKNOWN_KEYS_LINE,):
                        break
                    else:
                        m = _KEY_LINE.match(ln)
                        if m and got:
                            got[-1][1].append(m.group(2))
                with self.subTest(country=country):
                    self.assertEqual(got, want)
                    # A blank line before every heading but the first.
                    for h, _ks in want[1:]:
                        self.assertIn("\n\n" + h + "\n", settings)

    def test_account_tables_are_compact_type_first(self):
        for country, text in _renders():
            for m in re.finditer(r"(?m)^\[accounts\.[^\]]+\]\n((?:.+\n)*)",
                                 text):
                body = m.group(1).rstrip("\n").split("\n")
                with self.subTest(country=country, table=m.group(0)):
                    keys = [_KEY_LINE.match(ln) for ln in body]
                    if CT._UNKNOWN_KEYS_LINE in body:
                        body = body[:body.index(CT._UNKNOWN_KEYS_LINE)]
                    # Key lines only (a long list's continuation lines
                    # aside): no description, no blank line.
                    for ln in body:
                        self.assertFalse(ln.startswith("#"), ln)
                    keys = [k.group(2) for k in
                            (_KEY_LINE.match(ln) for ln in body) if k]
                    self.assertEqual(keys[0], "type")
                    self.assertEqual(keys, _expected_order("[accounts.", keys))
        text, _ = CT.render_init("canada", 2025)
        self.assertIn('[accounts.rrsp]\ntype      = "sheltered"\n'
                      'transfers = true\n', text)

    def test_reference_block_documents_every_account_key_type_first(self):
        for country in C.COUNTRIES:
            text, _ = CT.render_init(country, 2025)
            block = text.split("# [accounts.NAME]\n", 1)[1].split("\n\n")[0]
            keys = [m.group(2) for m in map(_KEY_LINE.match,
                                            block.split("\n")) if m]
            self.assertEqual(keys, ["type"] + sorted(
                k.name for k in CT.ACCOUNT_SPEC if k.name != "type"))
            # Every key under its description, no blank line in between.
            self.assertNotIn("\n\n", block)

    def test_no_fetch_broker_named_in_the_template(self):
        # The owner: the fetch keys stay documented, generically — no
        # broker the fetch plugin serves is singled out.
        for country, text in _renders():
            with self.subTest(country=country):
                self.assertNotRegex(text, r"(?i)questrade|ibkr_flex")
                for key in ("brokerage", "account", "query_id"):
                    self.assertRegex(text, rf"(?m)^# {key} +=")

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
                if ln.startswith("## ") \
                        and ln not in (CT._UNKNOWN_KEYS_LINE,
                                       CT._UNKNOWN_TOP_LINE,
                                       CT._UNKNOWN_TABLES_LINE,
                                       CT.NOTES_HEADING):
                    self.assertLessEqual(len(ln), CT.DOC_WIDTH + 3, ln)
        lines = CT._doc_lines("word " * 60)
        self.assertGreater(len(lines), 2)
        self.assertTrue(all(ln.startswith("## ") for ln in lines))

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
        self.assertRegex(r.text, r"\n## longer LEAPS for me\n"
                                 r"leaps_months +=")
        self.assertRegex(r.text, r"\n## Ontario\nprovince +=")
        # ... and otherwise exactly the template filled with the values:
        # the old descriptions and group headings are regenerated.
        mine = {"## longer LEAPS for me", "## Ontario"}
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
        self.assertFalse(CT.format_config(r.text).changed)

    def test_alphabetical_layout_is_regenerated_without_notes(self):
        # Files the alphabetical layout (before the grouped [settings]
        # and compact tables) wrote — init scaffolds and a file setting
        # keys in every table, both countries: every comment line is the
        # template's (by hash), nothing is kept as the user's.
        for path in sorted(_FIXTURES.glob("alphabetical_*.toml")):
            old = path.read_text(encoding="utf-8")
            doc = tomllib.loads(old)
            country = C.canonical_country(doc["settings"]["country"])
            with self.subTest(path=path.name):
                r = CT.format_config(old)
                self.assertTrue(r.changed)
                self.assertEqual((r.notes_lines, r.kept_comments), (0, 0))
                self.assertEqual(tomllib.loads(r.text), doc)
                self.assertEqual(list(tomllib.loads(r.text)["accounts"]),
                                 list(doc["accounts"]))
                self.assertEqual(r.text, CT.render_document(doc, country))
                self.assertNotIn("questrade", r.text.lower())
                self.assertFalse(CT.format_config(r.text).changed)

    def test_alphabetical_layout_keeps_the_users_comments(self):
        old = (_FIXTURES / "alphabetical_canada.toml").read_text(
            encoding="utf-8")
        mine = ["# my own note on LEAPS", "# mine, trailing",
                "# a note at the end of settings", "# about zeta"]
        text = (old.replace("\nleaps_months", f"\n{mine[0]}\nleaps_months", 1)
                .replace("\nyear                              = 2025\n",
                         "\nyear                              = 2025  "
                         f"{mine[1]}\n", 1)
                .replace("\n\n# One [accounts.NAME]",
                         f"\n\n{mine[2]}\n\n# One [accounts.NAME]", 1)
                .replace("\n[accounts.zeta]", f"\n{mine[3]}\n[accounts.zeta]",
                         1))
        for m in mine:
            self.assertIn(m, text)
        r = CT.format_config(text)
        self.assertEqual((r.notes_lines, r.kept_comments), (0, len(mine)))
        self.assertEqual(tomllib.loads(r.text), tomllib.loads(old))
        # (each now a `## ` prose line)
        mine = ["#" + m for m in mine]
        self.assertRegex(r.text, rf"\n{mine[0]}\nleaps_months +=")
        self.assertRegex(r.text, rf"\n{mine[1]}\nyear +=")
        self.assertRegex(r.text, rf"\n{mine[3]}\n\[accounts\.zeta\]\n")
        # (the last [settings] group: Transfers)
        self.assertRegex(r.text, rf"transfers_as_acquisitions += .*\n\n"
                                 rf"{mine[2]}\n\n"
                                 r"## One \[accounts\.NAME\]")
        again = CT.format_config(r.text)
        self.assertFalse(again.changed, "not idempotent")
        self.assertEqual(again.kept_comments, len(mine))

    def test_a_canadian_users_us_default_line_is_kept(self):
        # A line only the US template writes is a Canadian user's own.
        text = ('[settings]\ncountry = "ca"\nyear = 2025\n'
                '# tax_date = "trade"\n')
        r = CT.format_config(text)
        self.assertEqual(r.kept_comments, 1)
        self.assertIn('\n# tax_date = "trade"\n', r.text)

    def test_trailing_comment_moves_above_and_stays(self):
        text = ('[settings]\ncountry = "ca"  # mine\nyear = 2025\n'
                '[accounts.margin]   # main\ntype = "taxable"\n')
        r = CT.format_config(text)
        self.assertRegex(r.text, r"\n## mine\ncountry +=")
        self.assertRegex(r.text, r"\n## main\n\[accounts\.margin\]\n")
        self.assertFalse(CT.format_config(r.text).changed)
        for ln in r.text.splitlines():
            if not ln.startswith("#"):
                # (only the template's inline text ends a line)
                self.assertNotIn("# mine", ln)
                self.assertNotIn("# main", ln)

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
                 '# type = "x"', '# [accounts.old]', '## note', '### note',
                 '## leaps_months = 5', '#note = not a value']
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
        self.assertRegex(r.text, r'\n## was:\n# leaps_months = 6\n'
                                 r'leaps_months +=')
        self.assertRegex(r.text, r'\n# tax_date = "trade"\n'
                                 r'# tax_date +=')
        self.assertFalse(CT.format_config(r.text).changed)


class TestTrailingCommentContinuation(unittest.TestCase):
    """An end-of-line comment the old aligned layout wrapped onto the
    lines under it (`#` lines aligned under the comment, often padded
    with a second `#`) moves above its key with the first line, padding
    normalised — not to the next key. Synthetic values."""

    _HEAD = '[settings]\ncountry = "canada"\nyear = 2026\n'

    def _fmt(self, body):
        text = self._HEAD + body
        r = CT.format_config(text)
        self.assertEqual(tomllib.loads(r.text), tomllib.loads(text))
        self.assertFalse(CT.format_config(r.text).changed, "not idempotent")
        return r

    def _above(self, out, key):
        """The comment lines directly above `key`'s line, after its
        description (the template's own lines dropped)."""
        lines = out.split("\n")
        i = next(i for i, ln in enumerate(lines)
                 if re.match(rf"{key} +=", ln))
        j = i
        while j > 0 and lines[j - 1].startswith("#") \
                and not re.match(r"# ?[A-Za-z0-9_-]+ +=", lines[j - 1]):
            j -= 1
        return lines[j:i]

    def test_double_hash_padded_continuation_stays_with_its_key(self):
        r = self._fmt(
            "option_grant_timing_since = 2025      # first part of a note"
            " that goes on\n"
            "#                                     #   onto the next line"
            " — still this key\n"
            'option_buyback_loss_superficial = false\n')
        self.assertEqual(self._above(r.text, "option_grant_timing_since")[-2:],
                         ["## first part of a note that goes on",
                          "## onto the next line — still this key"])
        self.assertNotIn("onto the next line",
                         "\n".join(self._above(
                             r.text, "option_buyback_loss_superficial")))
        self.assertNotRegex(r.text, r"#\s{3,}#")
        self.assertEqual(r.kept_comments, 2)
        self.assertEqual(r.notes_lines, 0)

    def test_multi_line_continuations(self):
        # An indented '#' under the comment, a column-0 '#' padded out to
        # it, and the double-'#' form, one after the other.
        r = self._fmt(
            "leaps_months = 13            # one\n"
            "                             # two\n"
            "#                              three\n"
            "#                            #   four\n"
            "fx_cash_gains = true\n")
        self.assertEqual(self._above(r.text, "leaps_months")[-4:],
                         ["## one", "## two", "## three", "## four"])
        self.assertEqual(r.kept_comments, 4)

    def test_continuation_of_the_templates_own_comment(self):
        # The old init layout: the template's end-of-line text
        # (regenerated) with a line of the user's continuing it — the
        # user's line stays with its key; a continuation that is the
        # template's own text is regenerated, not kept.
        r = self._fmt(
            'tax_date = "settle"                 # settle | trade\n'
            "#                                   #   settle | trade\n"
            "#                                   #   my own reason\n"
            "leaps_months = 13\n")
        self.assertEqual(self._above(r.text, "tax_date")[-1:],
                         ["## my own reason"])
        self.assertRegex(r.text, r'\ntax_date += "settle" +# settle \| '
                                 r'trade\n')
        self.assertEqual(r.kept_comments, 1)
        self.assertNotIn("my own reason",
                         "\n".join(self._above(r.text, "leaps_months")))

    def test_table_line_continuation(self):
        r = self._fmt(
            '[accounts.margin]   # the main account,\n'
            '#                   #   opened long ago\n'
            'type = "taxable"\n')
        self.assertRegex(r.text, r"## the main account,\n## opened long ago\n"
                                 r"\[accounts\.margin\]\n")

    def test_comments_of_their_own_are_not_continuations(self):
        cases = {
            # a plain column-0 comment right under a trailing comment
            "plain": ("leaps_months = 13   # mine\n"
                      "# about the fee\n"
                      "fx_cash_gains = true\n"),
            # a blank line in between, even when padded
            "blank": ("leaps_months = 13   # mine\n\n"
                      "#                   #   about the fee\n"
                      "fx_cash_gains = true\n"),
            # no trailing comment to continue, even when indented
            "no trailing": ("leaps_months = 13\n"
                            "                    # about the fee\n"
                            "fx_cash_gains = true\n"),
        }
        for name, body in cases.items():
            with self.subTest(name):
                r = self._fmt(body)
                self.assertIn("about the fee",
                              "\n".join(self._above(r.text, "fx_cash_gains")))
                self.assertNotIn("about the fee",
                                 "\n".join(self._above(r.text,
                                                       "leaps_months")))

    def test_indented_table_keeps_a_comment_at_its_own_indent(self):
        # Keys indented under their table: a comment at the keys' indent
        # is the next key's, not a continuation.
        r = self._fmt(
            "[accounts.margin]\n"
            '  type = "taxable"   # mine\n'
            "  # about the plan\n"
            '  plan = "RRSP"\n')
        self.assertRegex(r.text, r"\n## about the plan\nplan +=")
        self.assertRegex(r.text, r"\n## mine\ntype +=")


# A line the old single-'#' layout wrote: its file header, before the
# `## ` prose mark (the rest of that layout is the current template's
# text with one '#').
_SINGLE_HASH_HEADER = (
    "# Each key's description is on the lines above it, the values it takes",
    "# after it. A commented key shows its default (or an example where it",
    "# has none); uncomment it to change it. `taxjson format` puts an edited",
    "# file back into this layout, keeping your values and comments.",
)


def _single_hash(text):
    """`text` as the layout before the `## ` prose mark wrote it."""
    new = [ln for ln in CT._FILE_HEADER if ln.startswith("## Each key")]
    i = CT._FILE_HEADER.index(new[0])
    head = "\n".join(CT._FILE_HEADER[i:]) + "\n"
    assert head in text
    text = text.replace(head, "\n".join("#" + ln for ln in
                                        _SINGLE_HASH_HEADER) + "\n")
    return "\n".join(ln[1:] if ln.startswith("##") else ln
                     for ln in text.split("\n"))


class TestProseMark(unittest.TestCase):
    """Prose comment lines start `## `, commented-out keys and tables a
    single `# ` (what you delete to switch one on); a value's end-of-line
    comment keeps a single `#`. `taxjson format` writes the user's own
    lines the same way. Synthetic values."""

    _KEYISH = re.compile(r"^##+\s*[A-Za-z0-9_\"-]+\s*=")

    def test_template_prose_is_double_hash_and_keys_single(self):
        for country, text in _renders():
            lines = text.split("\n")
            block = []
            blocks = []
            for ln in lines + [""]:
                if ln.startswith("#") and not ln.startswith("##"):
                    block.append(ln)
                    continue
                if block:
                    blocks.append(block)
                    block = []
                if ln.startswith("#"):
                    with self.subTest(country=country, line=ln):
                        # Prose: `## text` (or a bare `##`), never a
                        # commented-out key or table.
                        self.assertTrue(ln == "##" or ln.startswith("## "),
                                        ln)
                        self.assertNotRegex(ln, self._KEYISH)
                        self.assertFalse(CT._toml_code(ln[2:]), ln)
            self.assertTrue(blocks)
            for b in blocks:
                with self.subTest(country=country, block=b[0]):
                    # A run of single-'#' lines is commented-out TOML:
                    # `# key = value` / `# [table]` (and a multi-line
                    # value's lines), whole once the `# ` is deleted.
                    self.assertRegex(b[0], r"^# (\[|[A-Za-z0-9_-]+ *= )")
                    for ln in b:
                        self.assertTrue(ln.startswith("# "), ln)
                    self.assertTrue(tomllib.loads(
                        "\n".join(ln[2:] for ln in b)))

    def test_deleting_the_hash_switches_every_key_on(self):
        # The all-commented render: deleting `# ` from every single-'#'
        # line gives a file that sets every key; the `## ` prose stays.
        for country in C.COUNTRIES:
            bare = CT.render_document({"settings": {"country": country}},
                                      country, 2025)
            on = "\n".join(ln[2:] if ln.startswith("# ") else ln
                           for ln in bare.split("\n"))
            doc = tomllib.loads(on)
            with self.subTest(country=country):
                self.assertEqual(
                    set(doc["settings"]),
                    {k.name for k in CT.SETTINGS_SPEC
                     if CT.owned(country, "settings", k.name)})
                self.assertEqual(set(doc["accounts"]["NAME"]),
                                 {k.name for k in CT.ACCOUNT_SPEC})
                for t in CT.TABLES:
                    if CT.owned(country, t.name):
                        self.assertIn(t.name, doc)

    def test_end_of_line_comments_keep_a_single_hash(self):
        ca, _ = CT.render_init("canada", 2025)
        self.assertRegex(ca, r'(?m)^tax_date += "settle"  # settle \| trade$')
        self.assertNotRegex(ca, r"(?m)^[^#\n][^\n]*##")

    def test_comment_line_keeps_the_text(self):
        for line, code, want in (
                ("# a note", False, "## a note"),
                ("#a note", False, "## a note"),
                ("### a note", False, "## a note"),
                ("##   indented", False, "##   indented"),
                ("## x = 1", True, "# x = 1"),
                ("#x = 1", True, "# x = 1"),
                ("#", False, "##")):
            got = CT.comment_line(line, code)
            self.assertEqual(got, want, line)
            strip = lambda t: re.sub(r"^#+ ?", "", t)   # noqa: E731
            self.assertEqual(strip(got), strip(line))

    _USER = (
        '[settings]\ncountry = "ca"\nyear = 2025\n'
        "# a note about the year\n"
        "### a heading of mine\n"
        "#no blank after the hash\n"
        "## already double\n"
        "# note = see the folder (not a value)\n"
        '# province = "BC"\n'
        "\n"
        "## leaps_months = 4\n"
        "# my_own_key = 7\n"
        "\n"
        "# [accounts.old]\n"
        "###[[my_table]]\n"
        'fx_cash_gains = true  # trailing, mine\n'
        "[instalments]\n"
        'basis = "prior_year"\n'
        "# paid = [\n"
        '#   { date = 2025-03-15, amount = 100 },\n'
        "# ]\n"
        "withheld = 0\n")

    def _user_lines(self, text):
        return [ln for ln in text.split("\n") if ln.startswith("#")]

    def test_format_writes_user_prose_double_and_code_single(self):
        r = CT.format_config(self._USER)
        out = r.text
        lines = set(out.split("\n"))
        for prose in ("## a note about the year", "## a heading of mine",
                      "## no blank after the hash", "## already double",
                      "## note = see the folder (not a value)",
                      "## trailing, mine"):
            self.assertIn(prose, lines, out)
        for code in ('# province = "BC"', "# leaps_months = 4",
                     "# my_own_key = 7", "# [accounts.old]",
                     "# [[my_table]]", "# paid = [",
                     "#   { date = 2025-03-15, amount = 100 },", "# ]"):
            self.assertIn(code, lines, out)
        # Never a `## ` line that is TOML (`## key = value`); never `###`.
        for ln in out.split("\n"):
            if ln.startswith("##"):
                self.assertFalse(CT._toml_code(ln.lstrip("#")), ln)
        self.assertNotRegex(out, r"(?m)^###")
        # The user's commented multi-line value stays whole, above the
        # template's own commented `paid` (after its description).
        self.assertIn("## Instalments paid. No default.\n"
                      "# paid = [\n#   { date = 2025-03-15, amount = 100 },"
                      "\n# ]\n# paid                 = [", out)
        # A commented-out key (a double-'#' one too) joins its key, with
        # the lines of its block.
        self.assertRegex(out, r'## already double\n'
                              r'## note = see the folder \(not a value\)\n'
                              r'# province = "BC"\n# province +=')
        self.assertRegex(out, r"\n# leaps_months = 4\n# my_own_key = 7\n"
                              r"# leaps_months +=")
        self.assertEqual(r.notes_lines, 0)
        self.assertEqual(tomllib.loads(out), tomllib.loads(self._USER))
        # Every comment line of the user's kept, its text unchanged.
        mine = [ln for ln in self._USER.split("\n") if "#" in ln]
        self.assertEqual(r.kept_comments, len(mine))
        again = CT.format_config(out)
        self.assertFalse(again.changed, "not idempotent")
        self.assertEqual(again.kept_comments, r.kept_comments)

    def test_a_lone_line_of_a_commented_value_stays_code(self):
        # A value's element or closing bracket left on its own (an edit
        # that kept one line of a commented-out list): commented-out
        # TOML, not prose.
        text = ('[settings]\ncountry = "ca"\nyear = 2025\n'
                "[instalments]\nwithheld = 0\n"
                '#   { date = 2025-01-02, amount = 5, note = "x" },\n'
                "##   [1, 2],\n"
                "# [1] a footnote, prose\n"
                '# "quoted" prose\n')
        r = CT.format_config(text)
        for code in ('#   { date = 2025-01-02, amount = 5, note = "x" },',
                     "#   [1, 2],"):
            self.assertIn("\n" + code + "\n", r.text)
        for prose in ("## [1] a footnote, prose", '## "quoted" prose'):
            self.assertIn("\n" + prose + "\n", r.text)
        self.assertEqual(r.kept_comments, 4)
        self.assertFalse(CT.format_config(r.text).changed)
        self.assertTrue(CT._toml_code(" ]") and CT._toml_code("},"))

    def test_double_hash_prose_under_a_trailing_comment_is_its_own(self):
        # `## text` is the prose mark, not a continuation's padding.
        for body in ('[settings]\ncountry = "ca"\nyear = 2025\n'
                     "leaps_months = 13   # mine\n## about the fee\n"
                     "fx_cash_gains = true\n",
                     '[settings]\ncountry = "ca"\nyear = 2025\n'
                     "[accounts.a]  # mine\n## about the fee\n"
                     'type = "taxable"\n'):
            r = CT.format_config(body)
            self.assertRegex(r.text, r"\n## about the fee\n"
                                     r"(fx_cash_gains|type) +=")
            self.assertFalse(CT.format_config(r.text).changed)

    def test_single_hash_layout_regenerates_without_notes(self):
        # The layout before the prose mark (every prose line `# text`):
        # recognised as template text and rewritten, nothing kept.
        for country in C.COUNTRIES:
            for doc_text in (CT.render_init(country, 2025,
                                            tz="America/Toronto")[0],
                             CT.render_document(_full(country), country,
                                                2025)):
                old = _single_hash(doc_text)
                self.assertNotRegex(old, r"(?m)^##")
                self.assertIn(_SINGLE_HASH_HEADER[1], old)
                r = CT.format_config(old)
                with self.subTest(country=country):
                    self.assertEqual((r.notes_lines, r.kept_comments),
                                     (0, 0), r.text)
                    self.assertEqual(r.text, doc_text)

    def test_template_multi_line_value_with_a_user_line_stays_whole(self):
        # A commented `paid` the user edited one line of: kept whole as
        # the user's (the template's lines in it too), not split.
        text = CT.render_init("canada", 2025)[0].replace(
            '#   { date = "2025-05-20", amount = 12000, '
            'note = "refund transferred" },',
            '#   { date = "2025-05-21", amount = 99 },')
        r = CT.format_config(text)
        self.assertEqual(r.kept_comments, 4)
        self.assertIn('## Instalments paid. No default.\n'
                      '# paid                 = [\n'
                      '#   { date = "2025-03-16", amount = 15000 },\n'
                      '#   { date = "2025-05-21", amount = 99 },\n'
                      '# ]\n# paid                 = [\n', r.text)
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
