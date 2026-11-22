"""Re-audit-2 conformance gaps: a mutant of each rule below survived the
whole suite in the re-audit's conformance pass. Each test here fails on
that mutant and passes on the real code. Synthetic data only.

Formerly: test_fix_a2_spec_gaps
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _gains_one(country, book, sheltered=(), **req):
    """One country's pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         **dict(dict(year=2025), **req)))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res):
    return [t for t in res["transactions"] if t.get("qty")]


class TestUsConsumedTaxableReplacement(unittest.TestCase):
    """A2-0817 / US-WASH-21: a purchase in the loss's own account sold
    before the loss no longer washes it (an IRA one still does,
    US-WASH-11; one in another taxable account too, US-WASH-22 — owner
    decision on A2-0544, which replaced the other-account case pinned
    here). The dead 'fully consumed' branch that mutant USW06-stillheld
    edited is gone.

    Formerly: test_fix_a2_spec_gaps.TestUsConsumedTaxableReplacement"""

    def _book(self, rebuy_account):
        return [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000.0,
                   account="a"),
                tx("BUYSELL", "2025-02-10", "XYZ.US", 100, 4100.0,
                   account=rebuy_account),
                tx("BUYSELL", "2025-02-20", "XYZ.US", -100, 4200.0,
                   account=rebuy_account),
                tx("BUYSELL", "2025-03-01", "XYZ.US", -100, 4000.0,
                   account="a")]

    @rule(R.US_WASH_21)
    def test_taxable_replacement_sold_before_the_loss(self):
        # Same account: buy R, sell R, buy L, sell L at a loss — R's
        # shares were sold first in, first out before the loss.
        book = [tx("BUYSELL", "2025-02-10", "XYZ.US", 100, 4100.0,
                   account="a"),
                tx("BUYSELL", "2025-02-20", "XYZ.US", -100, 4200.0,
                   account="a"),
                tx("BUYSELL", "2025-02-21", "XYZ.US", 100, 5000.0,
                   account="a"),
                tx("BUYSELL", "2025-03-01", "XYZ.US", -100, 4000.0,
                   account="a")]
        r = _gains_one("usa", book)
        loss = [t for t in _sales(r) if t["date"] == "2025-03-01"][0]
        self.assertAlmostEqual(loss["gain"], -1000.0, places=2)
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)
        self.assertAlmostEqual(loss["permanently_disallowed"], 0.0)

    @rule(R.US_WASH_11)
    def test_ira_replacement_sold_before_the_loss(self):
        book = self._book("ira")
        r = _gains_one("usa", [t for t in book if t.account == "a"],
                       sheltered=[t for t in book if t.account == "ira"])
        loss = _sales(r)[0]
        self.assertAlmostEqual(loss["permanently_disallowed"], 1000.0,
                               places=2)


if __name__ == "__main__":
    unittest.main()
