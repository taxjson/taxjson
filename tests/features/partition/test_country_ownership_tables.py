"""Re-audit-2 partition fixes, lists partition-07 / partition-08 (area
partD), data half: IB Asia-Pacific trade dates, the generic importer's
crypto advice and futures prefixes, Coinbase stablecoin quotes in
property mode, Kraken ledger de-pegs, the country-ownership tables'
completeness check and the US explain trace's per-account FIFO.

Every fixture is SYNTHETIC: fake account ids (55500001 / U5550001),  # pii-ok
invented tickers (QZ*, XYZ, ZZS).

Formerly: test_fix_a2_partd_data
"""
import os
import sys
import unittest
import _paths


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO = _paths.REPO


# ---------------------------------------------------- ownership tables
class TestOwnershipTables(unittest.TestCase):
    """A2-0719: check_tax_rules verifies the country-ownership tables, and
    the Canada-only --foreign-roc dividend / --slip-gains are in them.

    Formerly: test_fix_a2_partd_data.TestOwnershipTables"""

    def _check(self):
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            import check_tax_rules
        finally:
            sys.path.pop(0)
        return check_tax_rules

    def test_tables_complete_today(self):
        self.assertEqual(self._check().ownership_problems(), [])

    def test_gaps_are_caught(self):
        from taxjson.lib import country as C
        chk = self._check()
        saved = (dict(C.FLAG_COUNTRY), dict(C.FLAG_VALUE_COUNTRY),
                 dict(C.COMMAND_COUNTRY))
        try:
            del C.FLAG_COUNTRY["--slip-gains"]           # help: no marker
            C.FLAG_COUNTRY["--no-such-flag"] = C.CANADA  # no CLI, no why
            del C.FLAG_VALUE_COUNTRY[("--foreign-roc", "dividend")]
            C.COMMAND_COUNTRY["no-such-command"] = C.USA
            probs = "\n".join(chk.ownership_problems())
        finally:
            C.FLAG_COUNTRY.clear()
            C.FLAG_COUNTRY.update(saved[0])
            C.FLAG_VALUE_COUNTRY.clear()
            C.FLAG_VALUE_COUNTRY.update(saved[1])
            C.COMMAND_COUNTRY.clear()
            C.COMMAND_COUNTRY.update(saved[2])
        self.assertIn("--no-such-flag is not an option", probs)
        self.assertIn("--no-such-flag is missing from country._FLAG_ATTRS",
                      probs)
        self.assertIn("FLAG_COUNTRY['--no-such-flag'] has no reason", probs)
        self.assertIn("--foreign-roc: its help says", probs)
        self.assertIn("no-such-command", probs)

    def test_value_level_refusal(self):
        from taxjson.lib.country import flag_country_problems
        self.assertTrue(flag_country_problems(
            "usa", {"--foreign-roc": "dividend"}))
        self.assertEqual(flag_country_problems(
            "usa", {"--foreign-roc": "acb"}), [])
        self.assertEqual(flag_country_problems(
            "canada", {"--foreign-roc": "dividend"}), [])
        self.assertTrue(flag_country_problems(
            "usa", {"--slip-gains": ["2025=1.00"]}))


if __name__ == "__main__":
    unittest.main()
