"""Regression tests for fix round 7 (wave B), group coreus: the engines'
income and US paths.

TJ-0647 a US stock dividend's entitled sale before a split was counted
        in pre-split shares: the dividend ratio came out wrong.
TJ-0648 an entitled sale under the ticker a rename replaced was not
        found: the held shares took the whole dividend.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7b_coreus
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _gains(book, sheltered=(), year=2025, country="usa", **req):
    """pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res, **match):
    return [t for t in res["transactions"]
            if t.get("qty") and all(t.get(k) == v for k, v in match.items())]


# ---------------------------------------------------------------- TJ-0723
class TestZeroBasisSpinOffKeepsTheParentsDates(unittest.TestCase):
    """10 parent shares bought 2024-01-02 for 100; a return of capital of
    100 on 2025-06-02 leaves them at zero basis; a §355 spin-off on
    2025-06-03 pays 2 shares with allocated basis 0; they are sold
    2025-08-01 for 10. The shares carry the parent's January 2024 date:
    a long-term gain of 10.

    Formerly: test_fix_1029_r7b_coreus.TestZeroBasisSpinOffKeepsTheParentsDates"""

    def _book(self, roc, alloc):
        from taxjson.model.transaction import TaxTransaction
        from taxjson.lib.corp_actions import CorporateAction, resolve_event
        ev = CorporateAction(
            date="2025-06-03", time="09:30:00", action_type="spinoff",
            source_symbol="ZQP.US", source_isin="", target_symbol="ZQC.US",
            target_isin="", ratio_new=1, ratio_old=5, qty_disposed=0,
            qty_received=2, fmv=0, currency="USD", target_currency="USD",
            account="margin", event_id="ev-zq-1")
        with contextlib.redirect_stderr(io.StringIO()):
            rows = resolve_event(ev, "tax_free_355", country="usa",
                                 hints={"allocated_acb": alloc})
        return ([tx("BUYSELL", "2024-01-02", "ZQP.US", 10, 100.0),
                 tx("ADJUST", "2025-06-02", "ZQP.US", 0, -roc, type="roc")]
                + [TaxTransaction(**r) for r in rows]
                + [tx("BUYSELL", "2025-08-01", "ZQC.US", -2, 10.0)])

    @rule(R.US_CORP_07)
    def test_zero_basis_parent(self):
        res = _gains(self._book(100.0, 0))
        sale = _sales(res, symbol="ZQC.US")[0]
        self.assertEqual(sale["acquired_date"], "2024-01-02")
        self.assertEqual(sale["term"], "LONG_TERM")
        self.assertAlmostEqual(sale["gain"], 10.0)
        self.assertNotIn("unapplied basis adjustment", res["_stderr"])

    @rule(R.US_CORP_07)
    def test_positive_basis_control(self):
        res = _gains(self._book(99.0, 0.2))
        sale = _sales(res, symbol="ZQC.US")[0]
        self.assertEqual(sale["acquired_date"], "2024-01-02")
        self.assertAlmostEqual(sale["gain"], 9.8)

    @rule(R.US_CORP_07)
    def test_spin_off_with_no_parent_lots_is_unchanged(self):
        """No parent shares in the books: the shares are a lot of the
        spin date, with the warning, and the $0 parent row adds none."""
        book = [r for r in self._book(100.0, 0)
                if not (r.symbol == "ZQP.US" and r.action != "ADJUST")
                and r.type != "roc"]
        res = _gains(book)
        sale = _sales(res, symbol="ZQC.US")[0]
        self.assertEqual(sale["acquired_date"], "2025-06-03")
        self.assertIn("finds no long ZQP.US lots", res["_stderr"])
        self.assertNotIn("unapplied basis adjustment", res["_stderr"])


if __name__ == "__main__":
    unittest.main()
