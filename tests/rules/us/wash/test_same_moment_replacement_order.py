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


# ------------------------------------------------------------ US wash
class TestUsSameMomentReplacementOrder(unittest.TestCase):
    """A2-0485, issue #31: same-moment replacements go taxable first,
    then the IRA, then by the accounts' name order (one account's rows
    in row order) — never by the order the books were merged in.

    Formerly: test_fix_a2_spec.TestUsSameMomentReplacementOrder"""

    def _book(self, first, second):
        return [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000,
                   account=first),
                tx("BUYSELL", "2025-03-03", "XYZ.US", -100, 4000,
                   account=first),
                tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                   account=first, time="11:00:00"),
                tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                   account=second, time="11:00:00")]

    @staticmethod
    def _cost(res):
        return {i["account"]: round(i["total_cost"], 2)
                for i in res["inventory"]}

    @rule(R.US_WASH_20)
    def test_name_order_not_merge_order(self):
        # The account whose name sorts first takes the 1,000 deferral,
        # whichever of the two sold and whichever was merged first.
        for first, second in (("z_first", "a_second"),
                              ("a_first", "z_second")):
            c = self._cost(_gains_one("usa", self._book(first, second)))
            lead = min(first, second)
            self.assertEqual(
                (c[lead], c[max(first, second)]), (5000.0, 4000.0))

    @rule(R.US_WASH_20)
    def test_taxable_before_the_ira(self):
        book = self._book("margin", "margin")[:3]
        ira = [tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                  account="ira", time="11:00:00")]
        # Same moment as the taxable rebuy: the taxable lot takes it.
        r = _gains_one("usa", book, sheltered=ira)
        loss = [t for t in r["transactions"] if t.get("qty")][0]
        self.assertAlmostEqual(loss["disallowed_amount"], 1000.0, places=2)
        self.assertAlmostEqual(loss.get("permanently_disallowed") or 0.0,
                               0.0, places=2)
        self.assertIn("taxable accounts first", _text("usa", "US-WASH-20"))


if __name__ == "__main__":
    unittest.main()
