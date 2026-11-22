"""The parser registry (step 5e): one entry per parser, from which the
detection tables and the brokerage tool's spellings are generated."""
import re
import unittest

from taxjson.lib.brokerages import detect
from taxjson.lib.brokerages.base import BaseBrokerage
from taxjson.parsers import registry
import _paths

REPO_ROOT = _paths.REPO


class TestRegistry(unittest.TestCase):
    def test_ids_and_spellings_are_distinct(self):
        ids = [s.id for s in registry.SPECS]
        self.assertEqual(len(ids), len(set(ids)))
        spellings = ids + [a for s in registry.SPECS for a in s.aliases]
        self.assertEqual(len(spellings), len(set(spellings)))
        # The name fallback's first match wins: no prefix or word twice.
        prefixes = [p for s in registry.SPECS for p in s.name_prefixes]
        words = [w for s in registry.SPECS for w in s.name_words]
        self.assertEqual(len(prefixes), len(set(prefixes)))
        self.assertEqual(len(words), len(set(words)))
        for s in registry.SPECS:
            self.assertIn(s.tier, registry.TIERS, s.id)

    def test_every_parser_imports_and_is_a_brokerage(self):
        for s in registry.SPECS:
            cls = registry.parser_class(s.id)
            self.assertTrue(issubclass(cls, BaseBrokerage), s.id)
            for a in s.aliases:
                self.assertIs(registry.parser_class(a), cls)
                self.assertEqual(registry.spec(a).id, s.id)
        self.assertEqual(registry.spec(" QT ").id, "questrade")
        bad = registry.ParserSpec("x", "X", (), "taxjson.lib.brokerages.nothing",
                                  "Nothing", 2, False, False, (), ())
        with self.assertRaises(ValueError) as cm:
            registry.parser_class(bad)
        self.assertIn("cannot load", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            registry.spec("nobody")
        self.assertEqual(str(cm.exception), "Unknown brokerage: nobody")
        self.assertIs(registry.parser_class(registry.spec("qt")),
                      registry.parser_class("questrade"))
        # The fold a recorded source brokerage takes (the fees report).
        self.assertEqual(registry.recorded_id("qt"), "questrade")
        self.assertEqual(registry.recorded_id("wealthsimple"), "wealthsimple")

    def test_the_detection_tables_are_the_registrys(self):
        from taxjson.lib.brokerages import base
        self.assertEqual(base.DISPLAY_NAMES, registry.display_names())
        self.assertIs(detect.DISPLAY_NAMES, base.DISPLAY_NAMES)
        self.assertEqual(detect.NAME_PREFIXES, registry.name_prefixes())
        self.assertEqual(detect.NAME_WORDS, registry.name_words())
        # Every parser but the generic importer (a mapping routes it) has
        # a content detector, in the registry's order.
        self.assertEqual(list(detect.DETECTORS),
                         [s.id for s in registry.SPECS if s.tier > 1])

    def test_the_corporate_action_brokers_are_the_registrys(self):
        from taxjson.bin.taxjson_corp_actions import EXTRACTORS
        from taxjson.pipeline.config import CORP_ACTION_BROKERS
        want = {s.id for s in registry.SPECS if s.corp_actions}
        self.assertEqual(set(EXTRACTORS), want)
        self.assertEqual(set(CORP_ACTION_BROKERS), want)

    def test_every_parser_has_its_page(self):
        text = (REPO_ROOT / "docs" / "brokers.md").read_text(encoding="utf-8")
        headings = set(re.findall(r"^## (.+)$", text, re.M))
        for s in registry.SPECS:
            if s.id != "generic":
                self.assertIn(s.display, headings, s.id)


if __name__ == "__main__":
    unittest.main()
