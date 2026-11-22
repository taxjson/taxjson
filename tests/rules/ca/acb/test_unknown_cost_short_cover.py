"""The 2026-10-29 fix round 3, group wash: the superficial-loss views
and the planning tools (backlog TJ-0600, TJ-0620, TJ-0599, TJ-0615).
Synthetic data only: made-up tickers (ZZR, OTH, SYN, ZZC, ZZQ) and
account numbers in throwaway folders.

Formerly: test_fix_1029_r3_wash"""
import contextlib
import io
import unittest
from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_short_cover import _short_cover_gains


class TestShortCoverIsNoPartialTaintLoss(unittest.TestCase):
    """TJ-0620: a short sold before the data (OPENING_BALANCE -10
    SYN.US), covered on 2025-06-02 for 500. The gains warned of an
    'unknown-cost loss of 500.00 with a buy on 2025-06-02 inside the
    window': the 'loss' was the cover cost (the short sale's proceeds
    are the unknown figure) and the 'buy' was the cover itself. A long
    sale's unknown cost counts as 0, so its raw loss is a loss: it is
    still flagged.

    Formerly: test_fix_1029_r3_wash.TestShortCoverIsNoPartialTaintLoss"""

    def _check_short(self, country):
        g = _short_cover_gains(country)
        self.assertEqual(
            [m["direction"] for m in g["manual_reporting_required"]],
            ["SHORT"])
        self.assertNotIn("superficial_loss_warnings", g)

    @rule(R.CA_ACB_12)
    def test_tj0620_a_short_cover_is_not_flagged(self):
        self._check_short("canada")

    @rule(R.US_BASIS_04)
    def test_tj0620_a_us_short_cover_is_not_flagged(self):
        self._check_short("usa")

    def _gains(self, rows):
        from taxjson.model.transaction import TaxTransaction as T
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            g = run_gains(
                [T(id=i, action=a, date=d, date_settle=d, symbol="SYN.TO",
                   quantity=q, net_amount=n, currency="CAD",
                   account="margin") for i, a, d, q, n in rows],
                req=GainsRequest(country="canada", year=2025, taxable=True,
                                 phantom_hint=False))
        return g, err.getvalue()

    @rule(R.CA_ACB_12)
    def test_tj0620_a_buy_after_the_cover_is_not_flagged_either(self):
        g, err = self._gains([
            ("open", "OPENING_BALANCE", "2024-01-02", -10, 0.0),
            ("cover", "BUYSELL", "2025-06-02", 10, -500.0),
            ("b2", "BUYSELL", "2025-06-09", 5, -250.0)])
        self.assertNotIn("superficial_loss_warnings", g)
        self.assertNotIn("superficial-loss check", err)

    @rule(R.CA_ACB_12)
    def test_tj0620_a_long_unknown_cost_loss_is_still_flagged(self):
        # 5 units held before the data and 5 bought for 500, all sold
        # for 200 (a loss whatever the 5 cost); 2 bought back a week on.
        g, err = self._gains([
            ("open", "OPENING_BALANCE", "2024-01-02", 5, 0.0),
            ("b1", "BUYSELL", "2025-01-06", 5, -500.0),
            ("s", "BUYSELL", "2025-03-03", -10, 200.0),
            ("b2", "BUYSELL", "2025-03-10", 2, -40.0)])
        w, = g["superficial_loss_warnings"]
        self.assertEqual((w["loss_date"], w["acquisition_date"],
                          w["raw_loss"]), ("2025-03-03", "2025-03-10", -300.0))
        self.assertIn("with a buy on 2025-03-10 inside the window", err)


if __name__ == "__main__":
    unittest.main()
