"""2026-10 workflow audit: the fixes the audit's confirmed findings asked
for (CHANGELOG "Fixed"): interest paid shown apart, a negative futures
price in a generic CSV, the audit over a pending election, the
checklist's hints, init's timezone, the single-folder year hints,
sanity with no holdings file, the headless election message, no empty
manifest.json, compare's exit codes (tests/test_compare.py).

Formerly: test_workflow_audit_fixes"""
import unittest
from _support.generic_parse import _HDR, _parse, _toml


class TestNegativeFuturesPrice(unittest.TestCase):
    """Formerly: test_workflow_audit_fixes.TestNegativeFuturesPrice"""
    def test_generic_keeps_the_notional_sign(self):
        rows = (_HDR + "2020-04-20,BUY,F:CLK0,1,-37.63,37620.5,9.5,USD\n"
                + "2020-04-21,SELL,F:CLK0,-1,10.01,10000.5,9.5,USD\n")
        txs, _, _ = _parse(rows, _toml())
        self.assertLess(txs[0]["net_amount"], 0)          # the buy received
        self.assertAlmostEqual(txs[0]["net_amount"], -37620.5)
        self.assertGreater(txs[1]["net_amount"], 0)
        # A positive price is booked as before.
        rows = (_HDR + "2025-12-01,BUY,F:ESZ5,1,6000,-300009.5,9.5,USD\n")
        txs, _, _ = _parse(rows, _toml())
        self.assertAlmostEqual(txs[0]["net_amount"], 300009.5)


if __name__ == "__main__":
    unittest.main()
