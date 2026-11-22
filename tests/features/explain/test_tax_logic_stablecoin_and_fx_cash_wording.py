"""Re-audit-2 test pins, group G2 (parsers and message fixes).

Each test pins a fix that held on the audited commit but that no test
failed on when reverted (A2-0176, A2-0521, A2-0538, A2-0897, A2-1569,
A2-1582, A2-1612, A2-0894, A2-1597). Every fixture is SYNTHETIC: fake
account ids, invented tickers and amounts.

Formerly: test_fix_a2_pins3_g2
"""
import unittest


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


import os


class TestTaxLogicWording(unittest.TestCase):
    """A2-1597 hunks bafb310_001 and 1f7045d_573 (tax_logic.py): Canada
    no longer says PYUSD/GUSD are cash on Coinbase only (S060-24: cash on
    Kraken too), and US-FX-02 says how fx-cash counts an unrated event.

    Formerly: test_fix_a2_pins3_g2.TestTaxLogicWording"""

    def test_no_canada_rule_says_coinbase_only(self):
        from taxjson.lib.tax_logic import catalog
        ca = catalog('canada')
        self.assertFalse([k for k, r in ca.items()
                          if 'Coinbase only' in r.text])
        self.assertIn('on Kraken and Coinbase alike', ca['CA-CRYPTO-02'].text)

    def test_us_fx_rule_names_unrated_fx_cash_events(self):
        from taxjson.lib.tax_logic import catalog
        self.assertIn('`tjs fx-cash` counts a cash event with no rate',
                      catalog('usa')['US-FX-02'].text)


if __name__ == '__main__':
    unittest.main()
