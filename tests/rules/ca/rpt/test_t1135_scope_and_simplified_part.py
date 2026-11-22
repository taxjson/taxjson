"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

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


# ------------------------------------------------------------ reports
class TestT1135Scope(unittest.TestCase):
    """A2-1473 (scope) and A2-0821 (the $250,000 Part A / Part B split).

    Formerly: test_fix_a2_spec.TestT1135Scope"""

    def _rep(self, net):
        from taxjson.bin.taxjson_t1135 import build_report
        from _support.ca_t1135_row import tx as t1135_tx
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "base.json"
            base.write_text(json.dumps({"transactions": [
                t1135_tx(date="2025-01-10", qty=100, net=net)]}))
            return build_report([base], [], 2025, {}, "CAD")

    @rule(R.CA_RPT_15)
    def test_verdict_is_on_these_books(self):
        rep = self._rep(50000.0)
        self.assertFalse(rep["filing_required"])
        self.assertEqual(rep["scope"], "books_only")
        self.assertIn("outside them", rep["scope_note"])
        self.assertIn("these books", _text("canada", "CA-RPT-15"))

    @rule(R.CA_RPT_13)
    def test_simplified_below_250k(self):
        self.assertTrue(self._rep(240000.0)["simplified_method_available"])
        self.assertFalse(self._rep(250000.0)["simplified_method_available"])


if __name__ == "__main__":
    unittest.main()
