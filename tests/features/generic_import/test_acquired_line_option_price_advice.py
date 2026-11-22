"""Fix round 7 (wave C) of 2026-10-29, the handoff and in-kind group:
handoff's lot checks with renamed accounts and re-described options,
ALLOWLOSS units, the in-kind contribution date on the registered side,
RESP withdrawal guidance, a per-share INKIND value with a contract
size, the ACQUIRED repair price for options and the SIMILAR warning
(synthetic data only).

Formerly: test_fix_1029_r7c_handoffinkind"""
import unittest


class TestAcquiredOptionRepair(unittest.TestCase):
    """TJ-0934: the price an ACQUIRED line's total agrees with is total
    / (quantity x contract size): an option's is per share, not 100
    times it.

    Formerly: test_fix_1029_r7c_handoffinkind.TestAcquiredOptionRepair"""

    def _advice(self, line, formula, expected):
        from taxjson.lib.tt_totals import Mismatch
        m = Mismatch("plan.tt:1", "110.00", formula, expected, repr(line))
        return " ".join(m.message("", line)[1])

    def test_option_price_is_per_share(self):
        line = ("ACQUIRED 2025-01-02 09:30:00 SYN251219C00010000.TO 1 CAD "
                "1 110 ARRIVED 2025-06-02")
        self.assertIn("(1.10)", self._advice(line, "qty*price*100+fee",
                                             "100.00"))
        self.assertNotIn("(110.00)", self._advice(
            line, "qty*price*100+fee", "100.00"))

    def test_mini_option_and_share(self):
        line = ("ACQUIRED 2025-01-02 09:30:00 SYN251219C00010000.TO 2 CAD "
                "1 110 ARRIVED 2025-06-02")
        self.assertIn("(5.50)", self._advice(line, "qty*price*10+fee",
                                             "20.00"))
        share = ("ACQUIRED 2025-01-02 09:30:00 SYN.TO 10 CAD 10 110 "
                 "ARRIVED 2025-06-02")
        self.assertIn("(11.00)", self._advice(share, "qty*price+fee",
                                              "100.00"))


if __name__ == "__main__":
    unittest.main()
