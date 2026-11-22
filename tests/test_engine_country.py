"""Each engine names its country through lib/country (step 4f of the
engine refactor): the shared stages read it, a subclass cannot leave it
unset, and the class attribute did not displace the US engine's docstring
(backlog TJ-1897, TJ-1899)."""
import unittest

from taxjson.lib import country as C
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.taxrules import TaxRules
from taxjson.engine.us.taxrules import USATaxRules


class TestEngineCountry(unittest.TestCase):
    def test_each_engine_names_its_country(self):
        self.assertEqual(CanadaTaxRules.country, C.CANADA)
        self.assertEqual(USATaxRules.country, C.USA)
        self.assertTrue(TaxRules.emit_replacement_stderr)

    def test_a_subclass_without_a_country_is_refused(self):
        with self.assertRaises(C.CountryError) as cm:
            class _Nameless(TaxRules):
                pass
        self.assertIn("TaxRules.country", str(cm.exception))
        with self.assertRaises(C.CountryError):
            class _Elsewhere(TaxRules):
                country = "atlantis"

    def test_a_subclass_keeps_its_parent_engine_country(self):
        with self.assertRaises(C.CountryError) as cm:
            class _Mixed(CanadaTaxRules):
                country = C.USA
        self.assertIn("inherits", str(cm.exception))

        class _Same(CanadaTaxRules):
            country = C.CANADA
        self.assertEqual(_Same.country, C.CANADA)

    def test_get_tax_rules_picks_by_the_engine_country(self):
        from taxjson.engine.by_country import get_tax_rules
        self.assertIsInstance(get_tax_rules("CA"), CanadaTaxRules)
        self.assertIsInstance(get_tax_rules("us"), USATaxRules)

    def test_the_us_engine_keeps_its_docstring(self):
        self.assertIn("FIFO", USATaxRules.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
