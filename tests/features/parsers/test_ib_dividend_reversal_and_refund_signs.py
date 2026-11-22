"""Regression tests for the 2026-07 audit tier-1 fixes:

1. run_to_file: a failed stage must not leave a truncated output file that the
   mtime cache then treats as fresh (previous good output must survive).
2. `tjs fees` counts standalone FEE-action rows (amount in net_amount).
3. IB dividend/withholding reversal rows keep their sign and net out instead
   of being abs()'d into extra income/tax.
4. Wash radar: 30-day windows are DST-immune (UTC-noon epochs) — day 30
   across the November fall-back is still inside the window.
5. Wash radar: the VIOLATION (substituted-property) test uses the CRA window
   around the LOSS SALE, not the last 30 days from today; fully-exited losses
   route to COOLING.
6. Engines apply one corporate SPLIT once even when merged multi-account
   input carries one SPLIT row per account.

Formerly: test_audit_tier1_fixes
"""
import os
from _tmpfiles import private_tmpfile
import unittest
from pathlib import Path


class TestIbReversalSigns(unittest.TestCase):
    """Formerly: test_audit_tier1_fixes.TestIbReversalSigns"""
    _CSV = (
        'Statement,Header,Field Name,Field Value\n'
        'Statement,Data,BrokerName,Interactive Brokers\n'
        'Dividends,Header,Currency,Account,Date,Description,Amount\n'
        'Dividends,Data,USD,U1,2026-03-15,'
        'AAA(US0000000001) Cash Dividend USD 2.50 per Share (Ordinary Dividend),250\n'
        'Dividends,Data,USD,U1,2026-03-20,'
        'AAA(US0000000001) Cash Dividend USD 2.50 per Share - Reversal (Ordinary Dividend),-250\n'
        'Dividends,Data,USD,U1,2026-03-20,'
        'AAA(US0000000001) Cash Dividend USD 2.40 per Share (Ordinary Dividend),240\n'
        'Withholding Tax,Header,Currency,Account,Date,Description,Amount\n'
        'Withholding Tax,Data,USD,U1,2026-03-15,'
        'AAA(US0000000001) Cash Dividend USD 2.50 per Share - US Tax,-37.50\n'
        'Withholding Tax,Data,USD,U1,2026-03-20,'
        'AAA(US0000000001) Cash Dividend USD 2.50 per Share - US Tax - Reversal,37.50\n'
        'Withholding Tax,Data,USD,U1,2026-03-20,'
        'AAA(US0000000001) Cash Dividend USD 2.40 per Share - US Tax,-36.00\n'
    )

    def _parse(self):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with private_tmpfile("w", suffix=".csv",
                             delete=False) as f:
            f.write(self._CSV)
            name = f.name
        try:
            return IbBrokerage().parse_file(Path(name))
        finally:
            os.remove(name)

    def test_dividend_reversal_nets_out(self):
        txs = self._parse()
        divs = [t for t in txs if t["action"] == "DIVIDEND"]
        self.assertEqual(len(divs), 3)
        total = sum(t["net_amount"] for t in divs)
        # +250 −250 +240: the correction nets to 240, not abs-summed 740.
        self.assertAlmostEqual(total, 240.0, places=2)

    def test_wht_refund_nets_out(self):
        txs = self._parse()
        taxes = [t for t in txs if t["action"] == "TAX"]
        self.assertEqual(len(taxes), 3)
        # Convention: positive = tax withheld. Charge 37.50, refund −37.50,
        # charge 36.00 → 36.00 net (was 111.00 with abs()).
        total = sum(t["net_amount"] for t in taxes)
        self.assertAlmostEqual(total, 36.0, places=2)
        self.assertAlmostEqual(max(t["net_amount"] for t in taxes), 37.5, places=2)
        self.assertAlmostEqual(min(t["net_amount"] for t in taxes), -37.5, places=2)


if __name__ == "__main__":
    unittest.main()
