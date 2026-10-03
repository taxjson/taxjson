"""Moved from the core's tests/test_wash_checks.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

buy-check / sell-check / verify — the advice they give.

These commands answer a yes/no question a user acts on with real
money, so the failure mode that matters is not a crash but CONFIDENT
WRONG ADVICE. Each test below pins a case where an earlier version
said something plausible and wrong:

  * printing a VIOLATION's sell-by deadline as a "safe to buy from"
    date (it is the LAST day to rescue the loss, roughly 30 days
    EARLIER than re-entry is actually safe),
  * telling a user to sell a sheltered-only holding "at a loss",
  * calling a violation rescueable when a registered account holds
    the same name (the matched portion is permanently denied),
  * taking the first date alphabetically across a class of
    cross-listings instead of the worst one.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import unittest


class TestQuestradePositionSymbols(unittest.TestCase):
    def test_class_share_is_not_mistaken_for_an_exchange(self):
        from taxjson_fetch.api import qt_position_symbol as q
        self.assertEqual(q("BRK.B"), "BRK.B.US")
        self.assertEqual(q("RDS.A"), "RDS.A.US")

    def test_real_exchange_suffixes_pass_through(self):
        from taxjson_fetch.api import qt_position_symbol as q
        self.assertEqual(q("SHOP.TO"), "SHOP.TO")
        self.assertEqual(q("ABC.VN"), "ABC.TO")   # one Canadian spelling (S010-05)
        self.assertEqual(q("AAPL"), "AAPL.US")

    def test_montreal_options_follow_the_underlying(self):
        from taxjson_fetch.api import qt_position_symbol as q
        self.assertEqual(q("BMO20Jan26C88.00", to_roots={"BMO"}),
                         "BMO260120C00088000.TO")
        self.assertEqual(q("BMO20Jan26C88.00"),
                         "BMO260120C00088000.US")


if __name__ == "__main__":
    unittest.main()
