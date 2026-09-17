"""Country-alias normalization for the corp-actions stage.

The template config advertises `country = "canada | ca | usa | us"` and the
gains engine accepts all four, but taxjson-corp-actions only knows the
canonical names. Normalizing in the run flow stops an alias like "ca" from
aborting the corp-actions stage at argparse, and gates the stage off entirely
for jurisdictions with no corp-action rules (US today).
"""
import unittest

from taxjson.bin.taxjson_run import _normalize_country, _country_has_corp_rules


class TestCountryNormalization(unittest.TestCase):
    def test_aliases_map_to_canonical(self):
        self.assertEqual(_normalize_country("ca"), "canada")
        self.assertEqual(_normalize_country("CA"), "canada")
        self.assertEqual(_normalize_country(" Canada "), "canada")
        self.assertEqual(_normalize_country("us"), "usa")
        self.assertEqual(_normalize_country("USA"), "usa")

    def test_corp_rules_for_both_countries(self):
        # Canada AND the US (plus their aliases) have corp-action election
        # rules; only an unknown jurisdiction skips the stage.
        self.assertTrue(_country_has_corp_rules("canada"))
        self.assertTrue(_country_has_corp_rules("ca"))
        self.assertTrue(_country_has_corp_rules("usa"))
        self.assertTrue(_country_has_corp_rules("us"))
        self.assertFalse(_country_has_corp_rules("germany"))


if __name__ == "__main__":
    unittest.main()
