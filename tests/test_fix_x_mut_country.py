"""Mutation pins for lib/country.py (audit G1-0): the small helpers the
suite never asserted directly."""
import argparse
import unittest

from taxjson.lib import country as C


class TestHelpers(unittest.TestCase):
    def test_is_canada_and_other_country(self):
        # m1603, m1620.
        self.assertTrue(C.is_canada(" CA "))
        self.assertFalse(C.is_canada("us"))
        self.assertEqual(C.other_country("canada"), "usa")
        self.assertEqual(C.other_country("US"), "canada")
        with self.assertRaises(C.CountryError):
            C.is_canada("mexico")

    def test_unknown_variant_falls_back_to_the_command(self):
        # m1632: a variant the table does not list reports the command
        # itself, with its reason.
        plain = C.command_country_problem("t1135", "usa")
        self.assertEqual(C.command_country_problem("t1135", "usa",
                                                   variant="x"), plain)
        self.assertIn("`taxjson t1135` is Canada-only (Form T1135", plain)
        self.assertIsNone(C.command_country_problem("t1135", "canada",
                                                    variant="x"))

    def test_country_argument_default_help(self):
        # m1644.
        p = argparse.ArgumentParser()
        C.add_country_argument(p)
        self.assertIn("Whose tax rules to apply", p.format_help())
        p = argparse.ArgumentParser()
        C.add_country_argument(p, help="mine")
        self.assertIn("mine", p.format_help())


if __name__ == "__main__":
    unittest.main()
