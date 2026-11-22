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


def _text(country, rid, **settings):
    """The rendered text of one rule under `settings`."""
    st = dict(settings, country=country, year=2025)
    for _t, rules in TL.rule_sections(country, st):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


# ------------------------------------------------------------ planning
class TestRadarIraReplacementIsPermanent(unittest.TestCase):
    """A2-1474: US-PLAN-01 carves out the IRA replacement the radar
    already reports as lost for good.

    Formerly: test_fix_a2_spec.TestRadarIraReplacementIsPermanent"""

    @rule(R.US_PLAN_01)
    def test_ira_rebuy_is_lost_for_good(self):
        from _support.planning_radar_checks import _row, _rows
        book = [_row("2025-01-10", "XYZ.US", 100, 5000.0, rid="b1",
                     currency="USD"),
                _row("2025-03-03", "XYZ.US", -100, 4000.0, rid="s1",
                     currency="USD")]
        ira = [_row("2025-03-10", "XYZ.US", 100, 4000.0, account="ira",
                    rid="i1", currency="USD")]
        row = _rows(book, "2025-03-20", sheltered=ira,
                    country="usa")["XYZ.US"]
        self.assertIn("WASHED", row["advisory"])
        self.assertIn("lost for good", row["advisory"])
        self.assertIn("IRA", _text("usa", "US-PLAN-01"))


if __name__ == "__main__":
    unittest.main()
