"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import os
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import tax_logic as TL


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestIncomeDatingCitesKnownIds(unittest.TestCase):
    """A2-1468: income_dating's docstring cites tax-logic ids only.

    Formerly: test_fix_a2_spec.TestIncomeDatingCitesKnownIds"""

    @rule(R.CA_INC_03)
    def test_docstring_ids_exist(self):
        import re
        from taxjson.lib import income_dating
        ids = set(re.findall(r"\[((?:CA|US)-[A-Z0-9-]+)\]",
                             income_dating.__doc__))
        self.assertIn("CA-INC-03", ids)
        known = set(TL.catalog())
        self.assertEqual(ids - known, set())


if __name__ == "__main__":
    unittest.main()
