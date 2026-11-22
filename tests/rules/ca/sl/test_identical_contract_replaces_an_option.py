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


class TestNothingButTheIdenticalContractReplacesAnOption(unittest.TestCase):
    """CA-SL-06: a long call's loss is replaced only by the identical
    contract. The audit's mutants (SL06-shares-replace-option: share buys
    admitted as candidates for an option loss; SL06-series: the call scan
    run for an option loss) are equivalent on the current engine — the
    still-held walk counts the loss contract's own class only, and the
    call scan compares the call's underlying with that class — so these
    tests pin the behaviour itself.

    Formerly: test_fix_a2_spec_gaps.TestNothingButTheIdenticalContractReplacesAnOption"""

    CALL = "ZZQ260116C00050000.US"

    def _loss_then(self, *rebuy):
        return _gains_one("canada", [
            tx("BUYSELL", "2025-06-02", self.CALL, 1, 500),
            tx("BUYSELL", "2025-07-01", self.CALL, -1, 200),
            tx("BUYSELL", "2025-07-08", *rebuy)])

    def _loss(self, res):
        return [t for t in _sales(res) if t["symbol"] == self.CALL][0]

    @rule(R.CA_SL_06)
    def test_shares_bought_do_not_replace_the_call(self):
        loss = self._loss(self._loss_then("ZZQ.US", 100, 5000))
        self.assertAlmostEqual(loss["gain"], -300.0, places=2)
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)

    @rule(R.CA_SL_06)
    def test_another_series_does_not_replace_the_call(self):
        loss = self._loss(self._loss_then("ZZQ260116C00055000.US", 1, 150))
        self.assertAlmostEqual(loss["gain"], -300.0, places=2)
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)

    @rule(R.CA_SL_06)
    def test_the_identical_contract_does(self):
        loss = self._loss(self._loss_then(self.CALL, 1, 150))
        self.assertAlmostEqual(loss["disallowed_amount"], 300.0, places=2)


if __name__ == "__main__":
    unittest.main()
