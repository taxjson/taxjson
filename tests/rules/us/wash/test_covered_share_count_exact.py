"""Fix round 2026-10-29 (round 4), group views: derived views and
reports.

TJ-0279  <account>.sum asked for a `.tt OPENING cost=unknown` line
         (MANUAL REPORTING REQUIRED) for a sale of units moved in from
         another of your accounts: their cost is in the blended figures
         (<account>_wash.sum, `tjs sum`). They are a block of their
         own now, and its holdings table says "unknown", not 0.00.
TJ-0598  reports/<account>_holdings.toml: units moved in from another
         of your accounts take the cost the run has after the passes
         across accounts (`cost_source = "blended"`), where round 3
         left it unknown.
TJ-0629  `tjs stats` / `win-rates` counted a registered account's
         withdrawal in kind (a TRANSFER out, `transfers = true`, which
         the books book as a sale at its value) as a closed trade, with
         a win or a loss. It is no trade: its P/L is counted apart.
TJ-0634  The US option-replacement warning's covered_shares carried
         float noise (9.999999999999998) into the gains JSON: the loss's
         open part is a money ratio's, and quantities are saved
         unrounded. Share counts are now at the engine's share
         precision (six decimals).

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r4_views
"""
import contextlib
import io
import json
import os
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# --------------------------------------------------------------------
# covered_shares at the engine's share precision (TJ-0634)
# --------------------------------------------------------------------

def _us_gains(book):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(list(book), [], [], req=GainsRequest(
            country="usa", taxable=True, year=2025))


class TestCoveredSharesHaveNoFloatNoise(unittest.TestCase):
    """TJ-0634: 100 shares sold at a loss, 90 bought back, then one
    call: its 10 shares against the loss are 10, in memory and in the
    gains JSON, not 9.999999999999998.

    Formerly: test_fix_1029_r4_views.TestCoveredSharesHaveNoFloatNoise"""

    CALL = "ZZQ250620C00050000.US"

    @rule(R.US_WASH_12)
    def test_ten_not_nine_point_nine_nine(self):
        from taxjson.lib.numeric import round_floats
        doc = _us_gains([
            tx("BUYSELL", "2025-01-02", "ZZQ.US", 100, -500),
            tx("BUYSELL", "2025-03-03", "ZZQ.US", -100, 400),
            tx("BUYSELL", "2025-03-10", "ZZQ.US", 90, -369),
            tx("BUYSELL", "2025-03-12", self.CALL, 1, -200)])
        (w,) = doc["option_replacement_warnings"]
        self.assertEqual(repr(w["covered_shares"]), "10.0")
        self.assertEqual(repr(w["replaced_qty"]), "90.0")
        # As the gains JSON saves it (quantities are not rounded there).
        saved = json.loads(json.dumps(round_floats(w)))
        self.assertEqual(repr(saved["covered_shares"]), "10.0")

    @rule(R.US_WASH_12)
    def test_a_fractional_share_keeps_its_six_decimals(self):
        from taxjson.engine.us.terms import share_qty
        self.assertEqual(share_qty(9.999999999999998), 10.0)
        self.assertEqual(share_qty(0.123456), 0.123456)
        self.assertEqual(repr(share_qty(-1e-12)), "0.0")


if __name__ == "__main__":
    unittest.main()
