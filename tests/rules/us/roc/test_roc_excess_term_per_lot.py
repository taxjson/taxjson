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


def _engine(country, rows):
    """compute_gains of one country's engine on TaxTransactions."""
    from taxjson.engine.by_country import get_tax_rules
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = get_tax_rules(country).compute_gains(list(rows))
    res["_stderr"] = err.getvalue()
    return res


class TestUsRocExcessTerm(unittest.TestCase):
    """US-ROC-02 (mutant USROC02-term): the part of a return of capital
    beyond a lot's basis is short- or long-term by that lot's holding
    period — the earlier test only had a short-term lot.

    Formerly: test_fix_a2_spec_gaps.TestUsRocExcessTerm"""

    @rule(R.US_ROC_02)
    def test_long_held_lot_gives_a_long_term_gain(self):
        res = _engine("usa", [
            tx("BUYSELL", "2023-01-10", "PRFD.US", 100, 1000.0),
            tx("BUYSELL", "2025-03-03", "PRFD.US", 100, 1000.0),
            tx("ADJUST", "2025-06-30", "PRFD.US", 0, -2400.0, type="roc",
               price=0.0)])
        deemed = sorted((g["acquired_date"], round(g["gain"], 2), g["term"])
                        for g in res["transactions"] if g.get("deemed"))
        self.assertEqual(deemed, [("2023-01-10", 200.0, "LONG_TERM"),
                                  ("2025-03-03", 200.0, "SHORT_TERM")])


if __name__ == "__main__":
    unittest.main()
