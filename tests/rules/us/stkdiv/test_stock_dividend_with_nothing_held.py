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


class TestUsStockDividendWithNothingHeld(unittest.TestCase):
    """A2-1486: the $0-lot fallback is stated; it never washes a loss.

    Formerly: test_fix_a2_spec.TestUsStockDividendWithNothingHeld"""

    @rule(R.US_STKDIV_03)
    def test_zero_lot_is_warned_and_not_a_replacement(self):
        from taxjson.lib.row_types import STOCK_DIVIDEND
        r = _gains_one("usa", [
            tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000),
            tx("BUYSELL", "2025-03-03", "XYZ.US", -100, 4000),
            tx("BUYSELL", "2025-03-10", "XYZ.US", 5, 0,
               type=STOCK_DIVIDEND)])
        sale = [t for t in r["transactions"] if t.get("qty")][0]
        self.assertAlmostEqual(sale["gain"], -1000.0, places=2)
        self.assertAlmostEqual(sale["disallowed_amount"], 0.0, places=2)
        self.assertEqual([(i["qty"], i["total_cost"])
                          for i in r["inventory"]], [(5.0, 0.0)])
        self.assertIn("warning: XYZ.US: stock dividend", r["_stderr"])
        self.assertIn("$0 purchase", _text("usa", "US-STKDIV-03"))


if __name__ == "__main__":
    unittest.main()
