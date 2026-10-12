"""docs/settings.md and docs/tax-rules.md stay complete.

docs/settings.md documents every key taxjson.toml accepts and every project
file's vocabulary; docs/tax-rules.md maps every tax-logic rule to its law and
code. AI assistants answer users from them, so a key or rule added without
its documentation fails here:

- every key the config validator accepts (lib/config_template.validator_keys:
  [settings], [accounts.NAME], [estimate], [carryover], [[distributions]],
  [[capital_gains_dividends]], [instalments]) and every retired setting has a
  `#### \\`key\\`` entry in its table's section of docs/settings.md;
- every ticker.map keyword (rename rules, lookups, market lists, retired),
  every .tt action (and the ACQUIRED shorthand), every generic-importer
  mapping section and key, every corporate-action election and hint, and every
  crypto-send decision is named in docs/settings.md;
- every rule id `taxjson tax-logic` knows (lib/tax_logic.catalog) is named in
  docs/tax-rules.md — directly or inside a range `X-01` … `X-05` — no unknown
  id is named, and the Canada part names no US id and the reverse (the
  Canada/USA partition).

Paths and symbols the two files name are checked by the knowledge-pack test.
"""
import re
import unittest
from pathlib import Path

from taxjson.bin import taxjson_convert_tt as tt
from taxjson.lib import config_check as cc
from taxjson.lib import config_template as tpl
from taxjson.lib import corp_actions as ca
from taxjson.lib import crypto_sends as cs
from taxjson.lib import tax_logic as tl
from taxjson.lib import ticker_map as tm
from taxjson.lib.brokerages import generic as gen

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = (ROOT / "docs" / "settings.md").read_text(encoding="utf-8")
RULES = (ROOT / "docs" / "tax-rules.md").read_text(encoding="utf-8")

# The heading of each validator table's section in docs/settings.md.
_TABLE_HEADING = {
    "settings": "### `[settings]`",
    "accounts": "### `[accounts.NAME]`",
    "estimate": "### `[estimate]`",
    "carryover": "### `[carryover]`",
    "distributions": "### `[[distributions]]`",
    "capital_gains_dividends": "### `[[capital_gains_dividends]]`",
    "instalments": "### `[instalments]`",
}


def _section(doc, heading, level="### "):
    """The text from `heading` to the next heading of its level or above."""
    i = doc.find(heading + "\n")
    if i < 0:
        return None
    rest = doc[i + len(heading):]
    stops = [m.start() for m in re.finditer(r"^#{1,%d} " % len(level.strip()),
                                            rest, re.M)]
    return rest[:stops[0]] if stops else rest


def _ticked(text):
    """Every `code` span in TEXT, read line by line outside fenced blocks
    (a ``` fence would pair the backticks of two lines)."""
    out, fenced = set(), False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.update(re.findall(r"`([^`]+)`", line))
    return out


def _entries(text):
    """The `#### \\`name\\`` entry names in TEXT."""
    return set(re.findall(r"^#### `([^`]+)`", text, re.M))


def _rule_ids(text):
    """Every rule id TEXT names, a range `X-01` … `X-05` expanded."""
    ids = set(re.findall(r"\b((?:CA|US)-[A-Z]+(?:-[A-Z0-9]+)+)\b", text))
    for a, b in re.findall(r"`((?:CA|US)-[A-Z]+-\d+)` … `((?:CA|US)-[A-Z]+-\d+)`",
                           text):
        pa, na = a.rsplit("-", 1)
        pb, nb = b.rsplit("-", 1)
        if pa == pb:
            ids.update(f"{pa}-{n:02d}" for n in range(int(na), int(nb) + 1))
    return ids


class TestSettingsDocKeys(unittest.TestCase):
    def test_every_validator_key_has_an_entry(self):
        missing = []
        for table, keys in tpl.validator_keys().items():
            sec = _section(SETTINGS, _TABLE_HEADING[table])
            if sec is None:
                missing.append(f"no section {_TABLE_HEADING[table]}")
                continue
            have = _entries(sec)
            missing += [f"[{table}] {k}" for k in keys if k not in have]
        self.assertEqual(missing, [], "docs/settings.md lacks these keys")

    def test_retired_settings_are_named(self):
        sec = _section(SETTINGS, _TABLE_HEADING["settings"])
        for k in cc.RETIRED_SETTINGS:
            self.assertIn(k, _entries(sec))

    def test_every_settings_group_is_a_heading(self):
        sec = _section(SETTINGS, _TABLE_HEADING["settings"])
        for name, _keys in tpl.SETTINGS_GROUPS:
            self.assertRegex(sec, r"(?m)^#### " + re.escape(name) + r"$")

    def test_no_entry_for_a_key_the_validator_refuses(self):
        # An entry for a key that is not accepted would send a user to an
        # "unknown key" warning.
        known = {k for keys in tpl.validator_keys().values() for k in keys}
        known |= set(cc.RETIRED_SETTINGS)
        for table, heading in _TABLE_HEADING.items():
            extra = _entries(_section(SETTINGS, heading)) - known
            self.assertEqual(extra, set(), f"{heading}: not validator keys")


class TestSettingsDocFiles(unittest.TestCase):
    def test_ticker_map_keywords(self):
        sec = _section(SETTINGS, "## ticker.map", level="## ")
        have = set(re.findall(r"^#### `([A-Z0-9]+)`", sec, re.M))
        want = (set(tm.RENAME_KEYWORDS) | set(tm.SIDE_KEYWORDS)
                | set(tm.RETIRED_KEYWORDS))
        self.assertEqual(want - have, set())

    def test_tt_actions(self):
        sec = _section(SETTINGS, "## .tt files", level="## ")
        named = set(re.findall(r"`([A-Z_]+)`", " ".join(
            re.findall(r"^#### (.*)$", sec, re.M))))
        want = set(tt._VALID_ACTIONS) | set(tt._SUGAR_ACTIONS)
        self.assertEqual(want - named, set())

    def test_generic_mapping_vocabulary(self):
        sec = _section(SETTINGS, "## Generic importer mapping", level="## ")
        want = {f"[{s}]" for s in gen._SECTIONS}
        for keys in (gen._COLUMN_KEYS, gen._FORMAT_KEYS, gen._DEFAULT_KEYS,
                     gen._BROKER_KEYS, gen._OPTIONS, gen._VALID_TARGETS):
            want |= set(keys)
        have = _ticked(sec)
        self.assertEqual(want - have, set())

    def test_elections_and_hints(self):
        want = {ca.IGNORE_ELECTION[0]}
        for rules in ca.RULES_BY_COUNTRY.values():
            for spec in rules.values():
                want |= {k for k, _ in spec.options}
        for hints in ca.HINTS_BY_ELECTION.values():
            want |= {h[0] for h in hints}
        have = _ticked(SETTINGS)
        self.assertEqual(want - have, set())

    def test_crypto_send_decisions(self):
        have = _ticked(SETTINGS)
        want = set(cs.DECISIONS) | {cs.FEE_DECISION}
        self.assertEqual(want - have, set())


class TestTaxRulesDoc(unittest.TestCase):
    def test_every_rule_id_is_mapped(self):
        named = _rule_ids(RULES)
        missing = sorted(set(tl.catalog()) - named)
        self.assertEqual(missing, [], "docs/tax-rules.md lacks these rules")

    def test_no_unknown_rule_id(self):
        unknown = sorted(_rule_ids(RULES) - set(tl.catalog()))
        self.assertEqual(unknown, [])

    def test_partition(self):
        ca_part, sep, us_part = RULES.partition("\n# Part 2")
        self.assertTrue(sep, "docs/tax-rules.md has no '# Part 2' (US)")
        self.assertEqual(sorted(i for i in _rule_ids(ca_part)
                                if i.startswith("US-")), [])
        self.assertEqual(sorted(i for i in _rule_ids(us_part)
                                if i.startswith("CA-")), [])

    def test_every_entry_has_its_fields(self):
        bad = []
        for e in re.split(r"(?m)^## ", RULES)[1:]:
            title = e.split("\n", 1)[0]
            for f in ("Rule", "Source", "Rule ids", "Code"):
                if not re.search(r"(?m)^- \*\*" + re.escape(f) + r":\*\*", e):
                    bad.append(f"{title}: no {f}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
