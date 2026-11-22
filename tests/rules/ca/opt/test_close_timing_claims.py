"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import contextlib
import io
import os
import unittest

from tax_rules import rule
from tax_rules.dual import tx
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


def _gains_one(country, book, sheltered=(), **req):
    """One country's pipeline.run_gains on `book` (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         **dict(dict(year=2025), **req)))
    res["_stderr"] = err.getvalue()
    return res


# ------------------------------------------------------------ options
class TestCloseTimingClaims(unittest.TestCase):
    """A2-0826: one id per claim under close timing, each pinned on the
    engine (forcing grant timing fails both).

    Formerly: test_fix_a2_spec.TestCloseTimingClaims"""

    _BOOK = [tx("BUYSELL", "2025-11-03", "ZZQ260116C00050000.US", -1, 300),
             tx("BUYSELL", "2026-01-05", "ZZQ260116C00050000.US", 1, 100)]

    def _year(self, year, timing="close"):
        r = _gains_one("canada", self._BOOK, year=year,
                       option_premium_timing=timing)
        return [t for t in r["transactions"] if t.get("symbol")]

    @rule(R.CA_OPT_05)
    def test_write_is_not_taxed_until_it_closes(self):
        self.assertEqual(self._year(2025), [])
        self.assertTrue(self._year(2025, timing="grant"))

    @rule(R.CA_OPT_10)
    def test_buy_back_realizes_premium_minus_cost(self):
        g = self._year(2026)
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0]["gain"], 200.0, places=2)
        self.assertIn("premium minus the cost",
                      _text("canada", "CA-OPT-10",
                            option_premium_timing="close"))


if __name__ == "__main__":
    unittest.main()
